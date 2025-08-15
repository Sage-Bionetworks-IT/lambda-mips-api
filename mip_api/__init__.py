import logging

from mip_api import balances, chart, s3, ssm, upstream, util


LOG = logging.getLogger(__name__)
LOG.setLevel(logging.DEBUG)


def lambda_handler(event, context):
    """
    Entry Point for Lambda

    Collect configuration from environment variables and query-string parameters,
    determine data requested based on the API endpoint called, and finally
    present the requested data in the desired format.

    Note: The Python process will continue to run for the entire lifecycle of
    the Lambda execution environment (15 minutes). Subsequent Lambda
    runs will re-enter this function.

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

    try:
        # collect environment variables
        mip_org = util.get_os_var("MipOrg")
        ssm_path = util.get_os_var("SsmPath")
        s3_bucket = util.get_os_var("CacheBucket")
        s3_prefix = util.get_os_var("CacheBucketPrefix")

        code_other = util.get_os_var("OtherCode")
        code_no_program = util.get_os_var("NoProgramCode")

        api_route_coa = util.get_os_var("ApiChartOfAccounts")
        api_route_tags = util.get_os_var("ApiValidTags")
        api_route_balances = util.get_os_var("ApiTrialBalances")

        _to_omit = util.get_os_var("CodesToOmit")
        omit_codes_list = util.parse_codes(_to_omit)

        # collect query-string parameters
        params = util.params_dict(event)
        hide_inactive = params["hide_inactive"]

        # build S3 cache paths, with separate paths for each combination
        # of endpoint and relevant parameters
        s3_path_gl_coa = s3_prefix + "gl-coa"
        if not hide_inactive:
            s3_path_gl_coa += "-full"
        s3_path_gl_coa += ".json"

        s3_path_program_coa = s3_prefix + "program-coa"
        if not hide_inactive:
            s3_path_program_coa += "-full"
        s3_path_program_coa += ".json"

        s3_path_balances = s3_prefix + "balances"
        if not hide_inactive:
            s3_path_balances += "-full"
        s3_path_balances += ".json"

        # get secure parameters
        ssm_secrets = ssm.get_secrets(ssm_path)

        # parse the path and return appropriate data
        if "path" in event:
            event_path = event["path"]

            if event_path == api_route_balances:
                # get chart of general ledger accounts
                gl_chart = chart.get_gl_chart(
                    mip_org,
                    ssm_secrets,
                    s3_bucket,
                    s3_path_gl_coa,
                    hide_inactive,
                )
                LOG.debug(f"Raw chart data: {gl_chart}")

                # get balance data
                raw_bal = balances.get_balances(
                    mip_org,
                    ssm_secrets,
                    s3_bucket,
                    s3_path_balances,
                    params["date"],
                )

                # combine them into CSV output
                balances_csv = balances.format_csv(raw_bal, gl_chart)
                return util.build_return_text(200, balances_csv)

            # common processing for '/accounts' and '/tags'

            # get chart of Program accounts
            _raw_program_chart = chart.get_program_chart(
                mip_org,
                ssm_secrets,
                s3_bucket,
                s3_path_program_coa,
                hide_inactive,
            )
            LOG.debug(f"Raw chart data: {_raw_program_chart}")

            # always process the chart of Program accounts
            _program_chart = chart.process_chart(
                _raw_program_chart,
                omit_codes_list,
                code_other,
                code_no_program,
                params,
            )

            # always limit the size of the chart
            program_chart = chart.limit_chart(_program_chart, params["limit"])

            if event_path == api_route_coa:
                # no more processing, return chart
                return util.build_return_json(200, program_chart)

            elif event_path == api_route_tags:
                # build a list of strings from the processed dictionary
                valid_tags = chart.list_tags(program_chart)
                return util.build_return_json(200, valid_tags)

            else:  # unknown API endpoint
                return util.build_return_json(404, {"error": "Invalid request path"})

        return util.build_return_json(
            400, {"error": f"Invalid event: No path found: {event}"}
        )

    except Exception as exc:
        LOG.exception(exc)
        return util.build_return_json(500, {"error": str(exc)})
