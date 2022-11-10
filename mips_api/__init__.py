from mips_api.mips import App
from mips_api.cache import S3Cache

import json


# these are globals so that they can be mocked in test
mips_app = None
s3_cache = None


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

    def _build_return(code, body):
        return {
            "statusCode": code,
            "body": json.dumps(body),
        }

    global mips_app
    if mips_app is None:
        mips_app = App()

    global s3_cache
    if s3_cache is None:
        s3_cache = S3Cache()

    if not mips_app.has_secrets():
        return _build_return(500, {"error": "No SSM secrets loaded"})

    if 'path' in event:
        event_path = event['path']

        # ignore leading emptry string from leading slash
        _, event_route, event_action = event_path.split('/', 2)

        if event_route == 'cache':
            print(f"Cache action requested: {event_path}")
            if event_action == 'refresh':
                try:
                    mips_app.refresh_cache()
                    return _build_return(201, "success")
                except Exception as exc:
                    print(f"Cache refresh exception: {exc}")
                    return _build_return(500, {"error": str(exc)})
            elif event_action == 'purge':
                try:
                    s3_cache.purge_cache()
                    return _build_return(201, "success")
                except Exception as exc:
                    print(f"Cache purge exception: {exc}")
                    return _build_return(500, {"error": str(exc)})
        else:
            try:
                cache_data = s3_cache.get_cache(event_path)

                print("Using cached data")
                return _build_return(200, cache_data)

            except Exception as exc:
                print(f"No S3 cache found: {exc}")
                mips_data = mips_app.get_mips_data(event_path)

                return _build_return(200, mips_data)

    return _build_return(400, {"error": "No event path found"})
