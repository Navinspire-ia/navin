"""The AWS gateway targets the test-02 inference account and its unlocked regions."""

import re
from pathlib import Path

AWS = Path(__file__).resolve().parents[1] / "deploy" / "aws"
TEST02 = "712493797892"
UNLOCKED = {"eu-north-1", "eu-west-3", "us-east-2", "eu-west-1", "eu-central-1", "us-east-1", "eu-west-2", "us-west-2"}


def _list(text: str, name: str) -> set[str]:
    m = re.search(name + r"[^\[]*\[([^\]]*)\]", text)
    assert m, name
    return set(re.findall(r'"([a-z0-9-]+)"', m.group(1)))


def test_provider_pinned_to_inference_account():
    main = (AWS / "main.tf").read_text()
    assert "allowed_account_ids = [var.aws_account_id]" in main
    variables = (AWS / "variables.tf").read_text()
    assert re.search(r'variable "aws_account_id"[^}]*default\s*=\s*"' + TEST02 + '"', variables)


def test_bedrock_regions_cover_every_unlocked_source_region():
    variables = (AWS / "variables.tf").read_text()
    block = variables.split('variable "bedrock_regions"', 1)[1]
    assert _list(block, "default") == UNLOCKED
    example = (AWS / "terraform.tfvars.example").read_text()
    assert _list(example, "bedrock_regions") == UNLOCKED
    assert f'aws_account_id     = "{TEST02}"' in example


def test_workload_accounts_can_assume_the_central_bedrock_role():
    variables = (AWS / "variables.tf").read_text()
    block = variables.split('variable "workload_account_ids"', 1)[1].split("}", 1)[0]
    assert set(re.findall(r'"(\d{12})"', block)) == {"575952383990", "784917519341"}
    main = (AWS / "main.tf").read_text()
    assert 'resource "aws_iam_role" "bedrock_workloads"' in main
    assert "arn:aws:iam::${id}:root" in main
    assert "bedrock_workloads_role_arn" in (AWS / "outputs.tf").read_text()
    sql = (AWS.parents[1] / "site/back/supabase/2026-10-16-bedrock-inference-account-test02.sql").read_text()
    for region in UNLOCKED:
        assert f"'{region}'" in sql
