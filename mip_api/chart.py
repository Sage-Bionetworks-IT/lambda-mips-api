import logging
import re

from mip_api import s3, upstream, util

LOG = logging.getLogger(__name__)
LOG.setLevel(logging.DEBUG)


def get_program_chart(org_name, secrets, bucket, path, hide_inactive):
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
    upstream_dict = upstream.get_chart(org_name, secrets, "Program", hide_inactive)
    LOG.debug(f"Upstream API response: {upstream_dict}")

    coa_dict = s3.cache(upstream_dict, bucket, path)
    return coa_dict


def get_gl_chart(org_name, secrets, bucket, path, hide_inactive):
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
    upstream_dict = upstream.get_chart(org_name, secrets, "GL", hide_inactive)
    LOG.debug(f"Upstream API response: {upstream_dict}")

    coa_dict = s3.cache(upstream_dict, bucket, path)
    return coa_dict


def process_chart(chart_dict, omit_list, other, no_program, params):
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
    if params["hide_inactive"]:
        code_len = 6

    # optionally move this list of codes to the top of the output
    priority_codes = params["priority_codes"]

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
    if params["show_other"]:
        out_chart = util.dict_prepend(out_chart, other, "Other")

    # inject "no program" code
    if params["show_no_program"]:
        out_chart = util.dict_prepend(out_chart, no_program, "No Program")

    return out_chart


def limit_chart(chart_dict, limit):
    """
    Optionally limit the size of the chart to the given number of high
    priority items based on a query-string parameter.
    """

    # "slice" the dictionary
    if limit > 0:
        # https://stackoverflow.com/a/66535220/1742875
        # split into two steps
        _dict_list = list(chart_dict.items())
        _short_dict = dict(_dict_list[:limit])
        return _short_dict

    return chart_dict


def list_tags(chart_dict):
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

    return tags
