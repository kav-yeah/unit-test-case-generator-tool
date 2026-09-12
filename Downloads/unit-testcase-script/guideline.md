### **Standard Guidelines for Writing Unit Test Cases**

This guideline provides a standardized approach for writing unit test cases across the repository. It ensures that all parts of the code are covered and each test case explicitly states the tested functionality and scenario.

What you are writing testcase for?
Purpose of Test Cases

HFconnect is a platform that acts as an integration interface between the HappyFox Helpdesk product and third-party services. It communicates with another repository, HFconnector, which interacts with third-party services, parses their responses into a format consumable by HappyFox Helpdesk, and sends them back to HFconnect. HFconnect, in turn, facilitates seamless communication with the Helpdesk, which serves as a ticketing platform.

---

### **1. Import Necessary Modules**
- Import required modules and functions from **Django, pytest, and other testing libraries**.
- Import the **models, views, repositories, and helpers** directly from the application.
- Avoid unnecessary imports to keep tests lightweight and relevant.

---

### **2. Set Up Fixtures**
- Use **pytest fixtures** to set up the necessary **database entries, dependencies, and Django test client**.
- Standard Fixtures for HFconnect
The following standard fixtures/models are already available in the path `auth/tests/conftest.py`. You must use these fixtures in your test cases and avoid creating new ones for the entities listed below:

    - Account
    - Authentication
    - Connection
    - Connector
    - ConnectorCategory
    - Product
    - Tenant

These models are defined in `core/models.py`. Always import the required fixtures from `auth/tests/conftest.py` and use them directly in your test cases to maintain consistency and avoid duplication.
  
- Keep fixture data **minimal** but **realistic**.

---

### **3. Write Test Cases**
- Write **unit tests for each method** in the view/class, covering different scenarios.
- Ensure that **each test case has a docstring** stating what it tests and the scenario it covers.
- Use **assertions** to verify the expected response, status codes, and output.

**Example Structure:**
```code change```
@method_decorator(authenticate_hfconnect, name="dispatch")
@method_decorator(csrf_exempt, name="dispatch")
class AccountDetailView(View):
    def get(self, request, account_reference):
        account_repository = AccountRepository(request.product)
        account = account_repository.get_account_by_reference(account_reference)
        if not account:
            return JsonResponse({"error": "Account not found"}, status=404)
        account = AccountSerializer(account).to_dict()
        return JsonResponse(account)

``` Existing Test structure in hfconnect```
import pytest
from django.urls import reverse
from unittest.mock import patch

@pytest.fixture
def client():
    return Client()

@pytest.fixture
def helpdesk_product():
    return Product.objects.create(name="helpdesk")


# Account fixture
@pytest.fixture
def helpdesk_account(helpdesk_product):
    reference = "dev"
    settings = {
        "timezone": {"name": "UTC, London, Lisbon, Dublin", "offset": "+00:00"},
        "domain_url": "http://dev.helpdesk.localhost",
        "credentials": {"access_token": "test_token"},
    }
    account = Account.objects.create(product=helpdesk_product, reference=reference, settings=settings)
    return account


@pytest.mark.django_db
class TestAuthenticateHFConnectDecorator:
    '''This class tests the authenticate_hfconnect decorator to avoid the decorator related testcases
    in other test files'''

    def test_without_passing_authentication_token_in_header(self, client, helpdesk_account):
        url = reverse('account-detail', kwargs={'account_reference': helpdesk_account.reference})
        headers = {
            'HTTP_X_PRODUCT_NAME':'helpdesk',
            'HTTP_X_HFCONNECT_REFERENCE':'dev',
        }
        response = client.get(url, **headers)
        assert response.status_code == 422
        assert response.json().get('error') == 'Authorization header is required'

        # when the header doesn't have the token prefix
        headers = {
            'HTTP_AUTHORIZATION':'mock_access_token',
            'HTTP_X_PRODUCT_NAME':'helpdesk',
            'HTTP_X_HFCONNECT_REFERENCE':'dev',
        }
        response = client.get(url, **headers)
        assert response.status_code == 422
        assert response.json().get('error') == 'Invalid Authorization header'

    def test_without_passing_product_name_in_header(self, client, helpdesk_account, ):
        url = reverse('account-detail', kwargs={'account_reference': helpdesk_account.reference})
        headers = {
            'HTTP_AUTHORIZATION':'Token mock_access_token',
            'HTTP_X_HFCONNECT_REFERENCE':'dev',
        }
        response = client.get(url, **headers)
        assert response.status_code == 422
        assert response.json().get('error') == 'X-Product-Name header is required'

        # when the request is for un-allowed product
        headers = {
            'HTTP_AUTHORIZATION':'Token mock_access_token',
            'HTTP_X_PRODUCT_NAME':'service_desk',
            'HTTP_X_HFCONNECT_REFERENCE':'dev',
        }
        response = client.get(url, **headers)
        assert response.status_code == 422
        assert response.json().get('error') == 'Product service_desk is not allowed'

    @patch('django.conf.settings.HELPDESK_HFCONNECT_ACCESS_TOKEN', 'mock_access_token')
    @patch('django.conf.settings.HFCONNECTOR_HFCONNECT_ACCESS_TOKEN', 'mock_access_token')
    def test_invalid_access_token_in_header(self, client, helpdesk_account):
        url = reverse('account-detail', kwargs={'account_reference': helpdesk_account.reference})
        headers = {
            'HTTP_AUTHORIZATION':'Token invalid_access_token',
            'HTTP_X_PRODUCT_NAME':'helpdesk',
            'HTTP_X_HFCONNECT_REFERENCE':'dev',
        }
        response = client.get(url, **headers)
        assert response.status_code == 401
        assert response.json().get('error') == 'Invalid access token'

    @patch('django.conf.settings.HELPDESK_HFCONNECT_ACCESS_TOKEN', 'mock_access_token')
    @patch('django.conf.settings.HFCONNECTOR_HFCONNECT_ACCESS_TOKEN', 'mock_access_token')
    def test_invalid_account_reference_in_header(self, client, helpdesk_account):
        url = reverse('account-detail', kwargs={'account_reference': 'invalid_account'})
        headers = {
            'HTTP_AUTHORIZATION':'Token mock_access_token',
            'HTTP_X_PRODUCT_NAME':'helpdesk',
            'HTTP_X_HFCONNECT_REFERENCE':'dev',
        }
        response = client.get(url, **headers)
        assert response.status_code == 404
        assert response.json().get('error') == 'Account not found'
---

### **4. Ensure Code Coverage**
- **Test only Happypath and negative case** of the function (happy path, error cases, and edge cases).
- **Write both positive and negative test cases**.
- If a scenario is unclear, **do not write a test** until it is clarified.

---

### **5. Do Not Mock Internal Method Calls**
- **Mock only external dependencies that are not imported in the file**.
- **Do not mock internal methods or imported functions from the same repository**.
  - Instead, **navigate to the actual definition** which you can find in the context provided to you and use its logic in the test.
  - This ensures the reliability of the test and prevents false positives.
- **Only use:**
  ```python
  from unittest.mock import Mock, patch
  ```
- Dont use 
```python 
 mock_account = mocker.Mock()
    mock_account.settings = {
        "domain_url": "https://example.com",
        "credentials": {"access_token": "test_token"}
    }
```
  - **Do not use mock as a fixture** or through other indirect methods.
- In case of external dependencies, Please use `patch` from `unittest.mock` for mocking dependencies in the test cases. Do **not** use any external mocking libraries (like `pytest-mock` or `mocker`). All mocks should be done using `patch` from `unittest.mock`.
 Avoid using `mocker.Mock()` or other mocking tools in the test case arguments.s

---

### **6. Test Case Naming & Structure**
- Test function names should be **descriptive and follow a `test_<scenario>` format**.
- Use **pytest markers** (`@pytest.mark.django_db`) where needed.
- Keep **test functions isolated** (avoid dependencies between tests).

---

### **7. Error Handling & Edge Cases**
- **Ensure proper error handling** (e.g., missing headers, incorrect data, unauthorized access).
- Test for:
  - ** Happypath Scenarios
  - **Missing or invalid input data**
  - **Unauthorized access attempts**
  - **Rate limits or API failures**
  - **Empty or malformed responses**