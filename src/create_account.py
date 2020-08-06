#!/usr/bin/env python
# -*- coding: utf-8 -*-

import json
import os
import warnings

from aws_lambda_powertools import Logger, Metrics, Tracer
import fastjsonschema

from controltowerapi.servicecatalog import ServiceCatalog
from controltowerapi.models import AccountModel
from .responses import build_response, error_response, authenticate_request

warnings.filterwarnings("ignore", "No metrics to publish*")

tracer = Tracer()
logger = Logger()
metrics = Metrics()
servicecatalog = ServiceCatalog()

CT_PORTFOLIO_ID = servicecatalog.get_ct_portfolio_id()
servicecatalog.associate_principal(CT_PORTFOLIO_ID, os.environ["LAMBDA_ROLE_ARN"])
CT_PRODUCT = servicecatalog.get_ct_product()

SCHEMA = {
    "type": "object",
    "properties": {
        "AccountName": {
            "type": "string",
            "pattern": "^[a-zA-Z0-9]{3,50}$",
            "minLength": 3,
            "maxLength": 50,
        },
        "AccountEmail": {"type": "string", "format": "email"},
        "ManagedOrganizationalUnit": {"type": "string"},
        "SSOUserEmail": {"type": "string", "format": "email"},
        "SSOUserFirstName": {"type": "string"},
        "SSOUserLastName": {"type": "string"},
        "CallbackUrl": {"type": "string", "format": "uri"},
        "CallbackSecret": {"type": "string"},
    },
    "required": [
        "AccountName",
        "AccountEmail",
        "ManagedOrganizationalUnit",
        "SSOUserEmail",
        "SSOUserFirstName",
        "SSOUserLastName",
    ],
}
VALIDATE = fastjsonschema.compile(SCHEMA)


@metrics.log_metrics(capture_cold_start_metric=True)
@tracer.capture_lambda_handler
@logger.inject_lambda_context(log_event=True)
def lambda_handler(event, context):
    if not event or "body" not in event:
        return error_response(400, "Unknown event")

    result = authenticate_request(event)
    if result is not True:
        return result

    try:
        body = json.loads(event["body"])
    except ValueError:
        return error_response(400, "Unable to parse JSON body")

    try:
        VALIDATE(body)
    except fastjsonschema.JsonSchemaException as error:
        return error_response(400, error.message)

    account_name = body["AccountName"]

    parameters = {
        "AccountName": account_name,
        "AccountEmail": body["AccountEmail"],
        "ManagedOrganizationalUnit": body["ManagedOrganizationalUnit"],
        "SSOUserEmail": body["SSOUserEmail"],
        "SSOUserFirstName": body["SSOUserFirstName"],
        "SSOUserLastName": body["SSOUserLastName"],
    }

    try:
        AccountModel.get(account_name)
        return error_response(400, f'Account name "{account_name}" already exists')
    except AccountModel.DoesNotExist:
        pass

    try:
        product = servicecatalog.provision_product(CT_PRODUCT, parameters)
    except Exception as error:
        logger.error(f"Unable to provision product: {str(error)}")
        return error_response(500, "Unable to provision product")

    item = {
        "account_name": account_name,
        "record_id": product["RecordId"],
        "state": product["Status"],
        "ou_name": body["ManagedOrganizationalUnit"],
        "created_at": product["CreatedTime"],
    }
    if "CallbackUrl" in body:
        item["callback_url"] = body["CallbackUrl"]
    if "CallbackSecret" in body:
        item["callback_secret"] = body["CallbackSecret"]

    account = AccountModel(**item)
    account.save(AccountModel.account_name.does_not_exist())

    return build_response(200, item)
