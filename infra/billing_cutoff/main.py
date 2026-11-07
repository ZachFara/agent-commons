"""Cloud Function: unlink billing from the project when the cutoff budget is reached.

Unlinking billing stops every Google Cloud service in the project (decision 0038). Re-link it
with `gcloud billing projects link`; see infra/README.md, "When an alert fires".
"""

import logging
import os

import functions_framework
import google.auth
from cutoff import over_budget, spend
from google.auth.transport.requests import AuthorizedSession

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("billing_cutoff")


def disable_billing(project_id: str) -> None:
    """Unlink the project from its billing account, as the function's service account."""
    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-billing"])
    response = AuthorizedSession(credentials).put(
        f"https://cloudbilling.googleapis.com/v1/projects/{project_id}/billingInfo",
        json={"billingAccountName": ""},
        timeout=30,
    )
    response.raise_for_status()
    log.warning("billing disabled for %s", project_id)


@functions_framework.cloud_event
def stop_billing(cloud_event: functions_framework.CloudEvent) -> None:
    """Handle one budget notification."""
    data = cloud_event.data["message"]["data"]
    cost, budget = spend(data)
    if not over_budget(data):
        log.info("under the cutoff: $%.2f of $%.2f", cost, budget)
        return
    log.warning("cutoff reached: $%.2f of $%.2f", cost, budget)
    disable_billing(os.environ["PROJECT_ID"])
