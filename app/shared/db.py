"""Database connection settings shared by the API and the Lambdas."""
import json
import os

import psycopg

CA_BUNDLE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "rds-ca.pem")


def conninfo(user: str, password: str) -> dict:
    return {
        "host": os.environ["DB_HOST"],
        "port": int(os.environ.get("DB_PORT", "5432")),
        "dbname": os.environ.get("DB_NAME", "quakes"),
        "user": user,
        "password": password,
        "sslmode": os.environ.get("DB_SSLMODE", "verify-full"),
        "sslrootcert": os.environ.get("DB_SSLROOTCERT", CA_BUNDLE),
        "connect_timeout": 5,
    }


def connect(user: str, password: str, **kwargs) -> psycopg.Connection:
    return psycopg.connect(**conninfo(user, password), **kwargs)


def read_secret(arn: str) -> dict:
    import boto3  # provided by the Lambda runtime; the API image never imports it

    return json.loads(boto3.client("secretsmanager").get_secret_value(SecretId=arn)["SecretString"])
