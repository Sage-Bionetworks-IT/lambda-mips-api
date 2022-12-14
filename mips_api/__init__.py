from mips_api import mips

import json
import os


# this is global so that it can be mocked in test
mips_app = None


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

    # get cache-control max-age from environment variable
    cache_age = None
    try:
        cache_age = os.environ['CacheTTL']
    except KeyError as exc:
        return {
            "statusCode": 500,
            "body": "Missing environment variable: CacheTTL",
        }

    # helper function to structure return data and set cache control
    def _build_return(code, body):
        return {
            "statusCode": code,
            "body": json.dumps(body),
            "headers": {"cache-control": f"max-age={cache_age}"},
        }

    # get secure parameters
    global mips_app
    try:
        if mips_app is None:
            mips_app = mips.App()
        mips_app.collect_secrets()
    except Exception as exc:
        return _build_return(500, {"error": str(exc)})

    # parse the path and get the data
    if 'path' in event:
        event_path = event['path']

        try:
            mips_data = mips_app.get_mips_data(event_path)
        except Exception as exc:
            return _build_return(500, {"error": str(exc)})

        return _build_return(200, mips_data)

    return _build_return(400, {"error": f"Invalid event: No path found: {event}"})
