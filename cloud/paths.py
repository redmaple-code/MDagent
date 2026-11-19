#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cloud 服务的落点约定。MDAGENT_HOME 默认 /opt/mdagent,测试可指 tmp。"""
import os

HOME = os.environ.get("MDAGENT_HOME", "/opt/mdagent")


def code_dir():
    return os.path.join(HOME, "code")


def accounts_dir():
    return os.path.join(HOME, "accounts")


def accounts_file():
    return os.path.join(accounts_dir(), "accounts.json")


def shared_evolution_dir():
    return os.path.join(HOME, "shared-evolution")


def users_dir():
    return os.path.join(HOME, "users")


def user_dir(username):
    return os.path.join(users_dir(), username)


def user_data_dir(username):
    return os.path.join(user_dir(username), "data")


def user_dsh_home(username):
    return os.path.join(user_dir(username), "dsh-home")


def user_conversations_dir(username):
    return os.path.join(user_dir(username), "conversations")


def user_projects_dir(username):
    return os.path.join(user_dir(username), "projects")


def jobs_dir():
    return os.path.join(HOME, "jobs")
