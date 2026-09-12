import os
import sys
import json
import time
import re
import importlib
import boto3
import github
from github import Github
from typing import Dict, Optional
import ast
from github.Repository import Repository
from github.PullRequest import PullRequest
from tabulate import tabulate
from pathlib import Path

# Set up GitHub API
GITHUB_TOKEN = os.getenv('GITHUB_TOKEN')
g = Github(GITHUB_TOKEN)

# Set up AWS Bedrock API
AWS_REGION = "us-west-2"
bedrock = boto3.client("bedrock-runtime", region_name=AWS_REGION)

MODEL_ID = "anthropic.claude-3-5-haiku-20241022-v1:0"

def fetch_pr_changes(repo_name: str, pr_number: int):
    """ Fetch changed Python files and their patches from a PR. """
    
    # Authenticate with GitHub API
    github_token = os.getenv("GITHUB_TOKEN")  # Ensure you have set this in your environment
    if not github_token:
        raise ValueError("⚠️ GITHUB_TOKEN is missing! Set it in your environment variables.")

    github = Github(github_token)
    repo: Repository = github.get_repo(repo_name)
    pr: PullRequest = repo.get_pull(pr_number)

    changes = {}

    for file in pr.get_files():
        if file.filename.endswith(".py"):
            changes[file.filename] = file.patch  # Store filename and diff content
    print(f"✅ Found {len(changes)} Python files in the PR.")
    return repo, pr, changes

def is_standard_or_third_party(module_name: str) -> bool:
    """ Check if a module is part of Python's standard library or a third-party package. """
    try:
        if module_name.startswith("."):
            return False 
        
        if module_name in sys.builtin_module_names:
            return True  # Standard module

        try:
            module_spec = importlib.util.find_spec(module_name)
        except ModuleNotFoundError:
            module_spec = None

        if module_spec is None:
            return False  # Likely a project module

        module_origin = module_spec.origin
        if module_origin and ("site-packages" in module_origin or "dist-packages" in module_origin):
            return True  # Third-party module

        if module_origin and "python" in module_origin:
            return True  # Standard library

        return False  # Project-specific module
    except Exception as e:
        print(f"⚠️ Error checking module type for {module_name}: {e}")
        return False

def resolve_import_path(import_path: str, base_path: str) -> str:
    """
    Convert a relative import path to an absolute file path within the repo.
    
    Example:
    - `from .oauth2 import OAuth2Handler` (inside `auth/handlers.py`)
      → Should look inside `auth/oauth2.py`
    
    Args:
        import_path (str): The module being imported (e.g., `.oauth2`).
        base_path (str): The file that contains this import (e.g., `auth/handlers.py`).

    Returns:
        str: The correct GitHub repo path to fetch.
    """
    if import_path.startswith("."):  # It's a relative import
        base_dir = "/".join(base_path.split("/")[:-1])  # Get the parent folder
        import_path = import_path.lstrip(".")  # Remove leading dot
        return f"{base_dir}/{import_path}.py"
    # import ipdb; ipdb.set_trace()
    return import_path.replace(".", "/") + ".py"  # Standard import resolution

def fetch_import_from_repo(repo, module, base_path=None, pr_number=None):
    """
    Fetch the actual implementation of imported components from the GitHub repository.
    
    - Handles both absolute and relative imports correctly.
    - Converts relative imports into their correct paths.
    - Checks if the file is part of the current PR before looking in the repo.
    """
    # import ipdb; ipdb.set_trace()
    module_path = resolve_import_path(module, base_path) if base_path else module.replace(".", "/") + ".py"
    # Check if the module is an external dependency
    external_dependencies = [
        "sentry_sdk",  # Add more external dependencies as needed
    ]
    
    if module in external_dependencies:
        print(f"Skipping external dependency {module}")
        return None  # Return None for external dependencies, as they aren't in the repo

    # Check if the file was added in the PR
    try:
        pr_files = repo.get_pull(pr_number).get_files()  # Adjust PR number dynamically as needed
        for pr_file in pr_files:
            if pr_file.filename.endswith(module_path):
                print(f"🔍 Found {module} in PR files.")
                return pr_file.patch, pr_file.filename   # Use the PR version if available

    except Exception as e:
        print(f"⚠️ Error checking PR files for {module_path}: {e}")
    
    # Fallback: Fetch from repo
    try:
        content_file = repo.get_contents(module_path, ref="main")  # Ensure branch is correct
        file_content = content_file.decoded_content.decode()  # Properly decode the content
        print(f"✅ Fetched {module} from repo.")
        # Not sure if the content_file is having name as a attr
        filename=Path(content_file.path).name
        return file_content, filename
    except Exception as e:
        print(f"⚠️ Error fetching {module} from GitHub API: {e}")  # Debugging error
        return None

def resolve_relative_import(module: str, base_path: str) -> str:
    """Convert relative imports to absolute module paths."""
    module_parts = module.lstrip(".").split(".")
    base_parts = base_path.replace(os.sep, ".").split(".")
    while module.startswith("."):
        module = module[1:]
        base_parts.pop()
    return ".".join(base_parts + module_parts)

def fetch_imported_components(repo, import_map, base_path, pr_number):
    """Fetch the actual implementation of imported components if they are project-specific."""
    component_definitions = {}

    for module, components in import_map.items():
        if module.startswith("."):  # Handle relative imports
            module = resolve_relative_import(module, base_path)

        if is_standard_or_third_party(module):
            print(f"✅ Skipping standard/third-party module: {module}")
            continue  # Skip built-in and third-party modules
        # import ipdb; ipdb.set_trace()
        result = fetch_import_from_repo(repo, module, base_path, pr_number)
        
        if result is None:
            print(f"⚠️ Skipping module {module} as it's either an external dependency or not found.")
            continue  # Skip processing if no result is returned

        module_code, module_filename = result 
        if module_code:
            if module_filename.endswith("__init__.py"):
                continue  # Skip package __init__.py files
            for component in components.split(", "):
                component_definitions[component] = extract_component_from_code(module_code, component)

def identify_all_imports(file_content: str) -> Dict[str, str]:
    """Extract all import statements from the file content."""
    import_statements = {}
    lines = file_content.split('\n')
    
    for line in lines:
        line = line.strip()
        if line.startswith("import ") or line.startswith("from "):  # Capture both import and from-import
            match_import = re.match(r"^\s*import\s+([\w\d_.]+)", line)
            match_from_import = re.match(r"^\s*from\s+([\w\d_.]+)\s+import\s+([\w\d_,\s]+)", line)

            if match_import:
                module = match_import.group(1)
                import_statements[module] = ""
            elif match_from_import:
                module, imported_items = match_from_import.groups()
                import_statements[module] = imported_items.strip()
    print(f"✅ Identified imports: {import_statements}")
    
    return import_statements

def extract_component_from_code(code: str, component_name: str) -> Optional[str]:
    """ Extracts the definition of a class or function from the provided module code. """
    try:
        tree = ast.parse(code)
        imports = []
        component_code = None
        
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):  # Collect relevant imports
                imports.append(ast.unparse(node))
            
            if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name == component_name:
                component_code = ast.unparse(node)  # Convert AST back to code
        
        if component_code:
            print(f"✅ Extracted {component_name} from code.")
            return "\n".join(imports) + "\n\n" + component_code if imports else component_code

    except Exception as e:
        print(f"⚠️ Error parsing {component_name}: {e}")
    
    return None  # Not found

def fetch_existing_test_cases(repo, filename, pr_number):
    """
    Fetch existing test cases from the repository and any new test cases added in the current PR.
    
    - Retrieves tests from existing test files in the repo.
    - Identifies new test cases added in the PR.
    """
    existing_tests = {}
    
    try:
        repo.get_contents("tests")  # Just check if directory exists
    except Exception as e:
        print(f"No Existing Test directory found for the file {filename} in the Repo, Checking for the test files in the PR")
        return existing_tests
    
    # 2️⃣ Check if new test cases were added in the PR
    try:
        pr_files = repo.get_pull(pr_number).get_files()
        for pr_file in pr_files:
            if pr_file.filename.startswith("tests/") and pr_file.filename.endswith(".py"):
                print(f"🔍 Found new test file in PR: {pr_file.filename}")
                pr_test_code = pr_file.patch
                try:
                    tree = ast.parse(pr_test_code)
                    for node in tree.body:
                        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                            existing_tests[node.name] = ast.unparse(node)  # Store new test cases
                except SyntaxError:
                    print(f"⚠️ Unable to parse new test file: {pr_file.filename}")
    except Exception as e:
        print(f"⚠️ Error fetching PR test files: {e}")

    # If we reach here, tests directory exists
    try:
        contents = repo.get_contents("tests", ref="main")  # Adjust path if needed
        for content_file in contents:
            if content_file.name.startswith("test_") and content_file.name.endswith(".py"):
                test_code = content_file.decoded_content.decode()
                tree = ast.parse(test_code)
                for node in tree.body:
                    if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                        existing_tests[node.name] = ast.dump(node)  # Store test function code
    except Exception:
        pass  # Handle other potential errors while processing test files
    
    return existing_tests

def fetch_file_content(repo, filename, pr_number, ref="main"):
    """Fetch the full content of the file from GitHub."""
    try:
        # import ipdb; ipdb.set_trace()
        pr_files = repo.get_pull(pr_number).get_files()
        for pr_file in pr_files:
            if pr_file.filename == filename:
                response = repo.get_contents(filename, ref=ref)
                file_content = response.decoded_content.decode()
                if file_content:
                    print(f"✅ Fetched content for {filename} from PR")
                    print(file_content)
                    return file_content
                else:
                    print(f"⚠️ Failed to fetch content from raw URL: {response.status_code}")
                    return None
        
        # If not modified in the PR, fetch from the main branch or the default ref
        content_files = repo.get_contents(filename, ref=ref)
        
        if isinstance(content_files, list):  # Multiple files returned
            # Filter the content_files to find the correct one
            for content_file in content_files:
                if content_file.path.endswith(filename):  # Match the filename based on the path
                    print(f"✅ Fetched content for {filename} from {content_file.path}")
                    return content_file.decoded_content.decode()
        
        elif isinstance(content_files, github.ContentFile.ContentFile):  # Single file returned
            print(f"✅ Fetched content for {filename} from {content_files.path}")
            return content_files.decoded_content.decode()

        print(f"⚠️ Failed to find {filename} in the repository")
        return None
        
    except Exception as e:
        print(f"⚠️ Error fetching file content: {e}")
        return None 
        
def generate_test_cases(repo, filename: str, patch: str, pr_number) -> Optional[str]:
    
    """ Generate test cases using Bedrock API. """
    # Fetch the full file content from the repo
    # import ipdb; ipdb.set_trace()
    full_file_content = fetch_file_content(repo, filename, pr_number)

    if not full_file_content:
        print(f"⚠️ Could not fetch the content for {filename}")
        return None

    # Identify all imports in the file
    resolved_imports = identify_all_imports(full_file_content)

    # Fetch the actual implementation of the imported components from the repo
    import_map = {}
    import_files_str = ""
    for module in resolved_imports:
        import_map[module] = resolved_imports[module]  # Add the components being imported

    # Fetch all the imported components to avoid unwanted mocking
    imported_files=fetch_imported_components(repo, import_map, filename, pr_number)

    existing_tests = fetch_existing_test_cases(repo, filename, pr_number)
    
    guideline = "guideline.md" if not ("models" in filename or "migrations/" in filename) else ""
    do_not_mock_imports = ""
    if resolved_imports:
        do_not_mock_imports = ", ".join(resolved_imports.keys())
        import_files_str = ", ".join(imported_files) if imported_files else ""

    messages = [
        {
        "role": "user",
        "content": f"""
        You are an expert Senior SDET proficient in Python. Please generate pytest unit test cases for the following Django code change in `{filename}`:

        ```
        {patch}
        ```

        existing_tests_note = f"\n\nExisting test cases for reference:\n```python\n{json.dumps(existing_tests, indent=4)}\n```" if existing_tests else ""

        Follow best practices:
        - The following test cases **already exist**: {', '.join(existing_tests.keys())}.  
        - **DO NOT** regenerate these tests. Only generate missing test scenarios or test cases which are not currently covered.
        - **Do Not** cover the testcases you are not entirely sure about.
        - Ensure **modularity and maintainability**.
        - **Do NOT mock the following classes: {do_not_mock_imports}. Instead, use the real implementations specified here: {import_files_str}.**
        - **Ensure that each test case includes at least one Assert statement** to validate the expected output, rather than just checking if the result is not None or empty.
        - Standard Fixtures for HFconnect:
            The following standard fixtures/models are already available in the path `auth/tests/conftest.py`. You must use these fixtures in your test cases and avoid creating new ones for the entities listed below:

                - Account
                - Authentication
                - Connection
                - Connector
                - ConnectorCategory
                - Product
                - Tenant

            These models are defined in `core/models.py`. Always import the required fixtures from `auth/tests/conftest.py` and use them directly in your test cases to maintain consistency and avoid duplication.
            If there is an import {do_not_mock_imports} from `models.py` that requires a database entry and is not covered in the above list, you can create a new fixture for it. Ensure the fixture is minimal, realistic, and reusable.

        **🚨 IMPORTANT 🚨**  
        Do **NOT** mock the following project-specific imports: {do_not_mock_imports}. Use the real implementations specified here: {import_files_str} instead.
        In case of external dependencies, Please use `patch` from `unittest.mock` for mocking dependencies in the test cases. Do **not** use any external mocking libraries (like `pytest-mock` or `mocker`). All mocks should be done using `patch` from `unittest.mock`. Avoid using `mocker.Mock()` or other mocking tools in the test case arguments.s

        **📝 REQUIREMENT:**
        - **Each test case MUST include a docstring** at the beginning.
        - The docstring should clearly describe:
            - The functionality being tested in this specific testcase.
            - The expected outcome asserted in the testcase.
            - Any edge cases being checked.

        **Example Format:**
        ```python
        def test_some_function():
            \"\"\"Tests some_function to ensure it correctly processes input X and handles edge case Y.\"\"\"
            # Test logic here...
        ```

        Follow the guidelines from `{guideline}` strictly. If the test cases listed in `{existing_tests}` are already present in the test file, do not regenerate them. Use them as additional reference, along with `{import_files_str}`, and only generate the missing test cases.

        **Do not include markdown syntax in the response** (e.g., no ```python or other markdown formatting).
        """
    }
    ]

    try:
        response = bedrock.invoke_model(
            modelId=MODEL_ID,
            body=json.dumps({
                "messages": messages,
                "max_tokens": 5000,
                "temperature": 0.7,
                "anthropic_version": "bedrock-2023-05-31"
            }),
            contentType="application/json",
            accept="application/json"
        )
        response_body = json.loads(response["body"].read())
        return response_body.get('content')[0].get("text")
    except Exception as e:
        print(f"❌ Error generating test cases: {e}")
        return None

def extract_test_scenarios(test_cases: str) -> Dict[str, str]:
    """
    Extracts test function names and maps them to scenario descriptions.
    """
    scenarios = {}
    lines = test_cases.split("\n")
    
    for line in lines:
        match = re.match(r"def (test_\w+)\(", line)
        if match:
            test_name = match.group(1)
            scenario_desc = " ".join(test_name.split("_")[1:]).capitalize()
            scenarios[scenario_desc] = test_name
    print(f"✅ Extracted Scenarios: {scenarios}")

    return scenarios

def main():
    repo_name = "happyfoxinc/hfconnect"
    pr_number = int(input("Enter your PR number: "))  # Change to actual PR number
    repo, pr, changes = fetch_pr_changes(repo_name, pr_number)

    if not changes:
        print("⚠️ No .py files changed in this PR. Exiting.")
        return

    test_cases_dict = {}
    scenario_mappings = {}

    for filename, patch in changes.items():
        # Skip test generation for models.py, urls.py, or any file inside migrations folder
        if filename.endswith("models.py") or filename.endswith("urls.py") or "migrations/" in filename:
            print(f"⏩ Skipping test generation for {filename}")
            continue

        test_cases = generate_test_cases(repo, filename, patch, pr_number)
        if test_cases:
            test_cases_dict[filename] = test_cases
            scenario_mappings.update(extract_test_scenarios(test_cases))

    if not test_cases_dict:
        print("⚠️ No test cases generated. Skipping PR creation.")
        return

    # Print generated test cases before asking for PR creation
    print("\nGenerated Test Cases:\n")
    for filename, test_cases in test_cases_dict.items():
        print(f"\n### Test Cases for {filename} ###\n")
        print(test_cases)
        print("\n" + "=" * 80 + "\n")

    if scenario_mappings:
        print("\n### Scenarios | Corresponding Unit Testcase Name ###\n")
    
        # Convert dictionary to list of lists for tabulate
        table_data = [[scenario, test_name] for scenario, test_name in scenario_mappings.items()]
    
        # Print the table with a proper format
        print(tabulate(table_data, headers=["Scenarios", "Corresponding Unit Testcase Name"], tablefmt="grid"))

    # Ask the user if they want to create a PR
    user_choice = input("Do you want to create a PR for the test cases? (yes/no): ").strip().lower()


    if user_choice == "yes":
        base_branch = pr.base.ref
        new_branch_name = f"testcase-pr-{pr.number}-{int(time.time())}"
        sha = repo.get_branch(base_branch).commit.sha

        repo.create_git_ref(ref=f"refs/heads/{new_branch_name}", sha=sha)

        for filename, test_cases in test_cases_dict.items():
            test_folder = os.path.join(os.path.dirname(filename), "tests")
            test_file_path = os.path.join(test_folder, f"test_{os.path.basename(filename)}")

            try:
                repo.get_contents(test_folder, ref=new_branch_name)
            except:
                repo.create_file(os.path.join(test_folder, "__init__.py"), "Initialize test folder", "", new_branch_name)

            try:
                existing_file = repo.get_contents(test_file_path, ref=new_branch_name)
                repo.update_file(test_file_path, f"Update test cases for {filename}", test_cases, existing_file.sha, new_branch_name)
            except:
                repo.create_file(test_file_path, f"Add test cases for {filename}", test_cases, new_branch_name)

        pr_body = f"Automatically generated unit test cases for PR #{pr.number}."
        new_pr = repo.create_pull(title=f"[Automated] Unit Test Cases for PR #{pr.number}", body=pr_body, head=new_branch_name, base=base_branch)
        print(f"\n✅ Created new PR: {new_pr.html_url}")
    else:
        print("\n⚡ PR creation skipped. Test cases printed above. ⚡")

if __name__ == "__main__":
    main()
