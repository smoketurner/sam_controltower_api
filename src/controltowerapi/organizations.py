#!/usr/bin/env python
# -*- coding: utf-8 -*-

import json
import logging

from aws_lambda_powertools import Logger
import boto3
import botocore

boto3.set_stream_logger("", logging.INFO)
logger = Logger()

CT_AUDIT_ACCOUNT_NAME = "Audit"
CT_LOG_ACCOUNT_NAME = "Log archive"
AI_OPT_OUT_POLICY_NAME = "AllOptOutPolicy"


class Organizations:
    def __init__(self) -> None:
        self.client = boto3.client("organizations")
        self.roots = []
        self.accounts = []

    def list_accounts(self) -> list:
        """
        List all of the accounts in an organization
        """
        if self.accounts:
            return self.accounts

        accounts = []

        paginator = self.client.get_paginator("list_accounts")
        page_iterator = paginator.paginate()
        for page in page_iterator:
            for account in page.get("Accounts", []):
                if account.get("Status") != "ACTIVE":
                    continue
                accounts.append(account)
        self.accounts = accounts
        return accounts

    def list_policies(self, policy_type: str) -> list:
        """
        List all of the policies in an organization
        """
        policies = []

        paginator = self.client.get_paginator("list_policies")
        page_iterator = paginator.paginate(Filter=policy_type)
        for page in page_iterator:
            policies.extend(page.get("Policies", []))
        return policies

    def list_roots(self) -> list:
        """
        List all the roots in an organization
        """
        if self.roots:
            return self.roots

        roots = []

        paginator = self.client.get_paginator("list_roots")
        page_iterator = paginator.paginate()
        for page in page_iterator:
            roots.extend(page.get("Roots", []))

        self.roots = roots
        return roots

    def enable_all_policy_types(self) -> None:
        """
        Enables all policy types in an organization
        """
        for root in self.list_roots():
            root_id = root["Id"]
            disabled_types = [
                policy_type.get("Type")
                for policy_type in root.get("PolicyTypes", [])
                if policy_type.get("Status") != "ENABLED"
            ]

            for disabled_type in disabled_types:
                logger.info(f"Enabling policy type {disabled_type} on root {root_id}")
                try:
                    self.client.enable_policy_type(
                        RootId=root_id, PolicyType=disabled_type
                    )
                    logger.debug(
                        f"Enabled policy type {disabled_type} on root {root_id}"
                    )
                except botocore.exceptions.ClientError as error:
                    if (
                        error.response["Error"]["Code"]
                        != "PolicyTypeAlreadyEnabledException"
                    ):
                        logger.exception("Unable to enable policy type")
                        raise error

    def get_ai_optout_policy(self) -> str:
        """
        Return the AI opt-out policy ID
        """

        for policy in self.list_policies("AISERVICES_OPT_OUT_POLICY"):
            if policy.get("Name") == AI_OPT_OUT_POLICY_NAME:
                logger.info(f"Found existing {AI_OPT_OUT_POLICY_NAME} policy")
                return policy.get("Id")

        logger.info(f"{AI_OPT_OUT_POLICY_NAME} policy not found, creating")

        policy = {
            "services": {
                "@@operators_allowed_for_child_policies": ["@@none"],
                "default": {
                    "@@operators_allowed_for_child_policies": ["@@none"],
                    "opt_out_policy": {
                        "@@operators_allowed_for_child_policies": ["@@none"],
                        "@@assign": "optOut",
                    },
                },
            }
        }

        try:
            response = self.client.create_policy(
                Content=json.dumps(policy),
                Description="Opt-out of all AI services",
                Name=AI_OPT_OUT_POLICY_NAME,
                Type="AISERVICES_OPT_OUT_POLICY",
            )
            policy_id = response.get("Policy", {}).get("PolicySummary", {}).get("Id")
            logger.debug(f"Created policy {AI_OPT_OUT_POLICY_NAME} ({policy_id})")
        except botocore.exceptions.ClientError as error:
            if error.response["Error"]["Code"] == "DuplicatePolicyException":
                return self.get_ai_optout_policy()
            raise error

        return policy_id

    def delete_ai_optout_policy(self) -> None:
        """
        Detaches and deletes the AI opt-out policy
        """

        policy_id = None
        for policy in self.list_policies("AISERVICES_OPT_OUT_POLICY"):
            if policy.get("Name") == AI_OPT_OUT_POLICY_NAME:
                policy_id = policy.get("Id")
                break

        if not policy_id:
            logger.info(f"Policy {AI_OPT_OUT_POLICY_NAME} already deleted")
            return

        self.detach_ai_optout_policy(policy_id)

        try:
            self.client.delete_policy(PolicyId=policy_id)
            logger.debug(f"Deleted policy {AI_OPT_OUT_POLICY_NAME} ({policy_id})")
        except botocore.exceptions.ClientError as error:
            if error.response["Error"]["Code"] != "PolicyNotFoundException":
                logger.exception("Unable to delete policy")
                raise error

    def attach_ai_optout_policy(self) -> None:
        """
        Attach the AI opt-out policy to the root
        """
        policy_id = self.get_ai_optout_policy()
        if not policy_id:
            logger.warn(f"Unable to find {AI_OPT_OUT_POLICY_NAME} policy")
            return

        for root in self.list_roots():
            root_id = root["Id"]
            logger.info(
                f"Attaching {AI_OPT_OUT_POLICY_NAME} ({policy_id}) to root {root_id}"
            )
            try:
                self.client.attach_policy(PolicyId=policy_id, TargetId=root_id)
                logger.debug(
                    f"Attached {AI_OPT_OUT_POLICY_NAME} ({policy_id}) to root {root_id}"
                )
            except botocore.exceptions.ClientError as error:
                if (
                    error.response["Error"]["Code"]
                    != "DuplicatePolicyAttachmentException"
                ):
                    logger.exception("Unable to attach policy")
                    raise error

    def detach_ai_optout_policy(self, policy_id: str = None) -> None:
        """
        Detach the AI opt-out policy from the root
        """
        if not policy_id:
            policy_id = self.get_ai_optout_policy()
            if not policy_id:
                logger.warn(f"Unable to find {AI_OPT_OUT_POLICY_NAME} policy")
                return

        for root in self.list_roots():
            root_id = root["Id"]
            logger.info(
                f"Detaching {AI_OPT_OUT_POLICY_NAME} ({policy_id}) from root {root_id}"
            )
            try:
                self.client.detach_policy(PolicyId=policy_id, TargetId=root_id)
                logger.debug(
                    f"Detached {AI_OPT_OUT_POLICY_NAME} ({policy_id}) from root {root_id}"
                )
            except botocore.exceptions.ClientError as error:
                if error.response["Error"]["Code"] != "PolicyNotAttachedException":
                    logger.exception("Unable to detach policy")
                    raise error

    def register_delegated_administrator(
        self, account_id: str, service_principal: str
    ) -> None:
        """
        Register a delegated administrator
        """

        logger.info(
            f"Registering {account_id} as a delegated administrator for {service_principal}"
        )

        try:
            self.client.register_delegated_administrator(
                AccountId=account_id, ServicePrincipal=service_principal
            )
            logger.debug(
                f"Registered {account_id} as a delegated administrator for {service_principal}"
            )
        except botocore.exceptions.ClientError as error:
            if error.response["Error"]["Code"] != "AccountAlreadyRegisteredException":
                logger.exception("Unable to register delegated administrator")
                raise error

    def get_audit_account_id(self) -> str:
        """
        Return the Control Tower Audit account
        """
        for account in self.list_accounts():
            if account.get("Name") == CT_AUDIT_ACCOUNT_NAME:
                return account["Id"]
        return None

    def get_log_account_id(self) -> str:
        """
        Return the Control Tower Log Archive account
        """
        for account in self.list_accounts():
            if account.get("Name") == CT_LOG_ACCOUNT_NAME:
                return account["Id"]
        return None
