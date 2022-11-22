import datetime
import os

import boto3

class S3Cache:
    _s3_prefix = 'cache'
    _default_max_age_days = 3

    s3_client = None
    s3_bucket = None
    max_age_days = None

    def __init__(self):
        if S3Cache.s3_client is None:
            S3Cache.s3_client = boto3.client('s3')

        if S3Cache.s3_bucket is None:
            try:
                S3Cache.s3_bucket = os.environ['CacheBucketName']
            except KeyError:
                raise Exception("The environment variable 'CacheBucketName' must be set.")

        if S3Cache.max_age_days is None:
            try:
                S3Cache.max_age_days = int(os.environ['CacheBucketExpirationDays'])
            except TypeError:
                raise Exception(
                    "The environment variable 'CacheBucketExpirationDays'"
                    " must be an integer.")
            except KeyError:
                S3Cache.max_age_days = S3Cache._default_max_age_days

    def get_cache(self, key):
        '''Read object from cache'''
        cache_key = S3Cache._s3_prefix + key

        obj = S3Cache.s3_client.get_object(
            Bucket=S3Cache.s3_bucket,
            Key=cache_key,
        )

        cache_date = obj['LastModified']
        print(f"Cache object modified time: {cache_date}")
        now = datetime.datetime.now(tz=cache_date.tzinfo)
        max_age = datetime.timedelta(days=S3Cache.max_age_days)

        if now - cache_date < max_age:
            cache_body = obj['Body'].read().decode('utf-8')
            print("Cached object")
            print(cache_body)
            return cache_body
        else:
            raise Exception(f'Cache is older than {max_age_days} days: {cache_date}')

    def put_cache(self, key, data):
        '''Write object to cache'''
        cache_key = S3Cache._s3_prefix + key
        S3Cache.s3_client.put_object(
            Bucket=S3Cache.s3_bucket,
            Key=cache_key,
            Body=data.encode(),
        )

        print(f"Object written to cache: {cache_key}")

    def purge_cache(self):
        '''Delete all objects in cache'''
        contents = S3Cache.s3_client.list_objects(
            Bucket=S3Cache.s3_bucket,
            Prefix=S3Cache._s3_prefix,
        )['Contents']

        param = { 'Objects': [ { 'Key': obj['Key'] } for obj in contents ] }
        print(f"Deleting objects: {param}")

        S3Cache.s3_client.delete_objects(
            Bucket=S3Cache.s3_bucket,
            Delete=param,
        )
