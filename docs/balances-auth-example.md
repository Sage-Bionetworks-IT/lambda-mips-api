# Accessing the /balances Endpoint

The `/balances` endpoint requires **AWS IAM authorization** (SigV4 signing).
Requests via CloudFront will receive a `403 Forbidden` response — you must call
the API Gateway URL directly.

## Prerequisites

1. **AWS credentials** with permission to invoke the endpoint. Your IAM
   user or role needs the following policy:

   ```json
   {
     "Effect": "Allow",
     "Action": "execute-api:Invoke",
     "Resource": "arn:aws:execute-api:<region>:<account-id>:<api-id>/Prod/GET/balances"
   }
   ```

   The stack exports this ARN as `BalancesExecuteArn` for use in downstream
   IAM policies.

2. **The API Gateway URL** (not the CloudFront URL). The stack outputs this as
   `ApiRouteTrialBalances`:

   ```
   https://<api-id>.execute-api.<region>.amazonaws.com/Prod/balances
   ```

## Option 1: AWS Console

Navigate to the API Gateway -> API -> Resources, and select `/Prod/balances`.
Navigate to the Test tab, enter query strings, and test.


## Option 2: AWS CLI

The `aws apigateway test-invoke-method` command invokes the endpoint directly
through the API Gateway control plane, bypassing the need for SigV4 signing
against the execute-api service. You need the REST API ID and the resource ID
for `/balances`.

```bash
# Find the REST API ID
aws apigateway get-rest-apis \
  --query "items[?name=='<stack-name>'].id" \
  --output text

# Find the resource ID for /balances
aws apigateway get-resources \
--rest-api-id <api-id> \
  --query "items[?path=='/balances'].id" \
  --output text

# Invoke the endpoint
aws apigateway test-invoke-method \
  --rest-api-id <api-id> \
  --resource-id <resource-id> \
  --http-method GET

# Or with a query string parameter:
aws apigateway test-invoke-method \
  --rest-api-id <api-id> \
  --resource-id <resource-id> \
  --http-method GET \
  --path-with-query-string "/balances?target_date=2026-03-15"
```

**Note:** This command requires `apigateway:TestInvokeMethod` permission rather
than `execute-api:Invoke`. It is useful for quick testing but does not exercise
the IAM authorizer — the request is always allowed if you have the control-plane
permission.


## Option 3: Python

```python
import boto3
import requests
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

# Configuration
region = "us-east-1"
api_url = "https://<api-id>.execute-api.us-east-1.amazonaws.com/Prod/balances"

# Get credentials from the default credential chain
session = boto3.Session()
credentials = session.get_credentials().get_frozen_credentials()

# Create and sign the request
request = AWSRequest(method="GET", url=api_url)
SigV4Auth(credentials, "execute-api", region).add_auth(request)

# Send the signed request
response = requests.get(api_url, headers=dict(request.headers))

print(response.status_code)
print(response.text)
```

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `403 Forbidden` with JSON body mentioning IAM | Using CloudFront URL | Use the API Gateway URL directly |
| `403 Forbidden` / `Missing Authentication Token` | Request not signed | Sign with SigV4 (see examples above) |
| `403 Forbidden` / `Access Denied` | IAM policy missing | Add `execute-api:Invoke` permission for the endpoint ARN |
| `401 Unauthorized` | Expired credentials | Refresh your AWS session/credentials |

## Notes

- The `/accounts` and `/tags` endpoints remain **public** and are accessible
  via CloudFront without authentication.
- The `target_date` query parameter (ISO 8601 format, e.g. `2026-03-15`) is
  optional. If omitted, the API uses today's date to determine the balance
  period.
