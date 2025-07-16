import json
import logging

from mip_api import chart, s3, ssm, upstream, util


LOG = logging.getLogger(__name__)
LOG.setLevel(logging.DEBUG)


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

    # helper functions to encapsulate the body, headers, and status code
    def _build_return(code, body):
        return {
            "statusCode": code,
            "body": json.dumps(body, indent=2),
        }

    try:
        # collect environment variables
        mip_org = util.get_os_var("MipsOrg")
        ssm_path = util.get_os_var("SsmPath")
        s3_bucket = util.get_os_var("CacheBucket")
        s3_path = util.get_os_var("CacheBucketPath")

        code_other = util.get_os_var("OtherCode")
        code_no_program = util.get_os_var("NoProgramCode")

        api_routes = {
            "ApiChartOfAccounts": util.get_os_var("ApiChartOfAccounts"),
            "ApiValidTags": util.get_os_var("ApiValidTags"),
        }

        _to_omit = util.get_os_var("CodesToOmit")
        omit_codes_list = util.parse_codes(_to_omit)

        # get secure parameters
        ssm_secrets = ssm.get_secrets(ssm_path)

        # get chart of accounts from mip cloud
        raw_chart = chart.get_chart(mip_org, ssm_secrets, s3_bucket, s3_path)
        LOG.debug(f"Raw chart data: {raw_chart}")

        # collect query-string parameters
        params = {}
        if "queryStringParameters" in event:
            params = event["queryStringParameters"]
            LOG.debug(f"Query-string parameters: {params}")

        # parse the path and return appropriate data
        if "path" in event:
            event_path = event["path"]

            # always process the chart of accounts
            mip_chart = chart.process_chart(
                params, raw_chart, omit_codes_list, code_other, code_no_program
            )

            if event_path == api_routes["ApiChartOfAccounts"]:
                # conditionally limit the size of the output
                return_chart = chart.limit_chart(params, mip_chart)
                return _build_return(200, return_chart)

            elif event_path == api_routes["ApiValidTags"]:
                # build a list of strings from the processed dictionary
                valid_tags = chart.list_tags(params, mip_chart)
                return _build_return(200, valid_tags)

            else:
                return _build_return(404, {"error": "Invalid request path"})

        return _build_return(400, {"error": f"Invalid event: No path found: {event}"})

    except Exception as exc:
        LOG.exception(exc)
        return _build_return(500, {"error": str(exc)})
