import json
import boto3

s3_client = None


def cache_read(bucket, path):
    """
    Read MIP response from S3 cache object
    """
    global s3_client
    if s3_client is None:
        s3_client = boto3.client("s3")

    data = s3_client.get_object(Bucket=bucket, Key=path)
    return json.loads(data["Body"].read())


def cache_write(data, bucket, path):
    """
    Write MIP response to S3 cache object
    """
    global s3_client
    if s3_client is None:
        s3_client = boto3.client("s3")

    body = json.dumps(data)
    s3_client.put_object(Bucket=bucket, Key=path, Body=body)
