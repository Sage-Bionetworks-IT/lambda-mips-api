from mips_api import mips

import json


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

    # helper function to encapsulate the body and status code
    def _build_return(code, body):
        return {
            "statusCode": code,
            "body": json.dumps(body),
        }

    global mips_app
    try:
        if mips_app is None:
            mips_app = mips.App()
        mips_app.collect_secrets()
    except Exception as exc:
        return _build_return(500, {"error": str(exc)})

    if 'path' in event:
        event_path = event['path']

        if event_path in mips.App.admin_routes:
            try:
                mips_app.admin_action(event_path)
                return _build_return(201, "success")
            except Exception as exc:
                return _build_return(500, {"error": str(exc)})

        cache_routes = mips_app._get_cache_routes()
        if event_path in cache_routes:
            try:
                mips_data = mips_app.get_mips_data(event_path)
            except Exception as exc:
                return _build_return(500, {"error": str(exc)})
            return _build_return(200, mips_data)

        return _build_return(404, {"error": "Invalid request path"})
    return _build_return(400, {"error": "Invalid event: No path found"})
