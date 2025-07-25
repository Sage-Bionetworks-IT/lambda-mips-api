import logging
import re

from mip_api import s3, upstream, util

LOG = logging.getLogger(__name__)
LOG.setLevel(logging.DEBUG)


def get_chart(org_name, secrets, bucket, path):
    """
    Access the Chart of Accounts from MIP Cloud, and implement a write-through
    cache of successful responses to tolerate long-term faults in the upstream
    API.

    A successful API response will be stored in S3 indefinitely, to be retrieved
    and used in the case of an API failure.

    The S3 bucket has versioning enabled for disaster recovery, but this means
    that every PUT request will create a new S3 object. In order to minimize
    the number of objects in the bucket, read the cache value on every run and
    only update the S3 object if it changes.
    """

    # get the upstream API response
    LOG.info("Read chart of accounts from upstream API")
    upstream_dict = upstream.program_chart(org_name, secrets)
    LOG.debug(f"Upstream API response: {upstream_dict}")

    # always read cached value
    LOG.info("Read cached chart of accounts from S3")
    cache_dict = None
    try:
        cache_dict = s3.cache_read(bucket, path)
        LOG.debug(f"Cached API response: {cache_dict}")
    except Exception as exc:
        LOG.exception("S3 read failure")

    if upstream_dict:
        # if we received a non-empty response from the upstream API, compare it
        # to our cached response and update the S3 write-through cache if needed
        if upstream_dict == cache_dict:
            LOG.debug("No change in chart of accounts")
        else:
            # store write-through cache
            LOG.info("Write updated chart of accounts to S3")
            try:
                s3.cache_write(upstream_dict, bucket, path)
            except Exception as exc:
                LOG.exception("S3 write failure")
        coa_dict = upstream_dict
    else:
        # no response (or an empty response) from the upstream API,
        # rely on our response cached in S3.
        coa_dict = cache_dict

    if not coa_dict:
        # make sure we don't return an empty value
        raise ValueError("No valid chart of accounts found")

    return coa_dict


def process_chart(params, chart_dict, omit_list, other, no_program):
    """
    Process chart of accounts to remove unneeded programs,
    and inject some extra (meta) programs.

    5-digit codes are inactive and should be ignored in most cases.
    8-digit codes are active, but only the first 6 digits are significant,
      i.e. 12345601 and 12345602 should be deduplicated as 123456.
    """

    # deduplicate on shortened numeric codes
    # pre-populate with codes to omit to short-circuit their processing
    found_codes = []
    found_codes.extend(omit_list)

    # output object
    out_chart = {}

    # whether to filter out inactive codes
    code_len = 5
    if util.param_inactive_bool(params):
        code_len = 6

    # optionally move this list of codes to the top of the output
    priority_codes = util.param_priority_list(params)

    # add short codes
    for code, _name in chart_dict.items():
        if len(code) >= code_len:
            # truncate active codes to the first 6 significant digits
            short = code[:6]
            # enforce AWS tags limitations
            # https://docs.aws.amazon.com/tag-editor/latest/userguide/best-practices-and-strats.html
            # enforce removing special characters globally for consistency,
            # only enforce string limit when listing tag values because the string size will change.
            regex = r"[^\d\w\s.:/=+\-@]+"
            name = re.sub(regex, "", _name)

            if short in found_codes:
                LOG.info(f"Code {short} has already been processed")
                continue

            if priority_codes is not None:
                if short in priority_codes:
                    out_chart = util.dict_prepend(out_chart, short, name)
                else:
                    out_chart[short] = name
            else:
                out_chart[short] = name
            found_codes.append(short)

    # inject "other" code
    if util.param_other_bool(params):
        out_chart = util.dict_prepend(out_chart, other, "Other")

    # inject "no program" code
    if util.param_no_program_bool(params):
        out_chart = util.dict_prepend(out_chart, no_program, "No Program")

    return out_chart


def limit_chart(params, chart_dict):
    """
    Optionally limit the size of the chart to the given number of high-
    priority items based on a query-string parameter.
    """

    # if a 'limit' query-string parameter is defined, "slice" the dictionary
    limit = util.param_limit_int(params)
    if limit > 0:
        # https://stackoverflow.com/a/66535220/1742875
        short_dict = dict(list(chart_dict.items())[:limit])
        return short_dict

    return chart_dict


def list_tags(params, chart_dict):
    """
    Generate a list of valid AWS tags. Only active codes are listed.

    The string format is `{Program Name} / {Program Code}`.

    Returns
        A list of strings.
    """

    tags = []

    # build tags from chart of accounts
    for code, name in chart_dict.items():
        # enforce AWS tags limitations
        # https://docs.aws.amazon.com/tag-editor/latest/userguide/best-practices-and-strats.html
        # max tag value length is 256, truncate
        # only enforce when listing tag values
        tag = f"{name[:245]} / {code[:6]}"
        tags.append(tag)

    limit = util.param_limit_int(params)
    if limit > 0:
        LOG.info(f"limiting output to {limit} values")
        return tags[0:limit]
    else:
        return tags
