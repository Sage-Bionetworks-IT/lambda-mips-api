import os

import json
import boto3
import requests

ssm_client = None
if ssm_client is None:
    ssm_client = boto3.client('ssm')

_mips_url_login = 'https://login.abilaonline.com/api/v1/sso/mipadv/login'
_mips_url_chart = 'https://mipapi.abilaonline.com/api/v1/maintain/chartofaccounts'
_mips_url_logout = 'https://mipapi.abilaonline.com/api/security/logout'

def lambda_handler(event, context):
    """Sample pure Lambda function

    Parameters
    ----------
    event: dict, required
        API Gateway Lambda Proxy Input Format

        Event doc: https://docs.aws.amazon.com/apigateway/latest/developerguide/set-up-lambda-proxy-integrations.html#api-gateway-simple-proxy-for-lambda-input-format

    context: object, required
        Lambda Context runtime methods and attributes

        Context doc: https://docs.aws.amazon.com/lambda/latest/dg/python-context-object.html

    Returns
    ------
    API Gateway Lambda Proxy Output Format: dict

        Return doc: https://docs.aws.amazon.com/apigateway/latest/developerguide/set-up-lambda-proxy-integrations.html
    """

    valid_routes = []

    try:
        valid_routes.append(os.environ['apiAllCostCenters'])
    except KeyError:
        raise Exception("The environment variable 'apiAllCostCenters' must be set.")

    try:
        mips_org = os.environ['MipsOrg']
    except KeyError:
        raise Exception("The environment variable 'MipsOrg' must be set.")

    try:
        ssm_path = os.environ['SsmPath']
    except KeyError:
        raise Exception("The environment variable 'SsmPath' must be set.")

    try:
        ssm_secrets = collect_secrets(ssm_path)
    except KeyError:
        raise Exception("The environment variable 'MipsOrg' must be set.")

    # parse the path and get the data
    if 'path' in event:
        event_path = event['path']

        try:
            mips_data = collect_mips_data(event_path, ssm_secrets, mips_org)
        except Exception as exc:
            return _build_return(500, {"error": str(exc)})

        return _build_return(200, mips_data)

    return _build_return(400, {"error": f"Invalid event: No path found: {event}"})


# helper function to structure return data and set cache control
def _build_return(code, body):
    return {
        "statusCode": code,
        "body": json.dumps(body)
    }

def collect_secrets(self, ssm_path):
    '''Collect secure parameters'''
    ssm_secrets = {}

    params = ssm_client.get_parameters_by_path(
        Path=ssm_path,
        Recursive=True,
        WithDecryption=True,
    )

    if 'Parameters' in params:
        for p in params['Parameters']:
            # strip leading path plus / char
            if len(p['Name']) > len(self.ssm_path):
                name = p['Name'][len(self.ssm_path)+1:]
            else:
                name = p['Name']
            return p['Value']
            print(f"Loaded secret: {name}")
    else:
        raise Exception(f"Invalid response from SSM client")

def collect_mips_data(self, ssm_secrets, mips_org):
    '''Log into MIPS, get the chart of accounts, and log out'''

    try:
        # get mips access token
        mips_creds = {
            'username': ssm_secrets['user'],
            'password': ssm_secrets['pass'],
            'org': mips_org,
        }
        login_response=requests.post(
            _mips_url_login,
            json=mips_creds,
        )
        login_response.raise_for_status()
        access_token = login_response.json()["AccessToken"]

        # Per Finance we filter the results taking just the records where segment=Program and status=A
        # There are about 150 records. We use a page size of 500 to get all the results in a single request.
        chart_params = {
            "filter[segmentId]":"Program",
            "filter[accountStatusId]":"A",
            "page[size]":"500"
        }
        chart_response=requests.get(
            _mips_url_chart,
            chart_params,
            headers={"Authorization-Token":access_token},
        )
        chart_response.raise_for_status()
        accounts = chart_response.json()["data"]

        # save chart of accounts as a dict mapping code to title, e.g. { '990300': 'Platform Infrastructure' }
        for a in accounts:
            self.mips_dict[a['accountCodeId']] = a['accountTitle']

    except Exception as exc:
        print('Error interacting with mips')
        raise exc

    finally:
        # It's important to logout. Logging in a second time without logging out will lock us out of MIPS
        requests.post(
            _mips_url_logout,
            headers={"Authorization-Token":access_token},
        )

