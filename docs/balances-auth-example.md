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

## Option 2: Python (requests + botocore SigV4)

```python
import requests
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.session import Session

# Configuration
region = "us-east-1"
api_url = "https://<api-id>.execute-api.us-east-1.amazonaws.com/Prod/balances"

# Get credentials from the default credential chain
session = Session()
credentials = session.get_credentials().get_frozen_credentials()

# Create and sign the request
request = AWSRequest(method="GET", url=api_url)
SigV4Auth(credentials, "execute-api", region).add_auth(request)

# Send the signed request
response = requests.get(api_url, headers=dict(request.headers))

print(response.status_code)
print(response.text)
```

## Option 3: Python (boto3 + urllib)

```python
import json
from urllib.request import Request, urlopen
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
import boto3

# Configuration
region = "us-east-1"
api_url = "https://<api-id>.execute-api.us-east-1.amazonaws.com/Prod/balances"

# Get credentials
session = boto3.Session()
credentials = session.get_credentials().get_frozen_credentials()

# Sign the request
request = AWSRequest(method="GET", url=api_url)
SigV4Auth(credentials, "execute-api", region).add_auth(request)

# Send using urllib (no extra dependency)
req = Request(api_url, headers=dict(request.headers))
with urlopen(req) as resp:
    print(resp.status)
    print(resp.read().decode())
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
