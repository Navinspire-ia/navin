/**
 * Catalog of the Ops (DevOps / SysOps / infra) actions.
 *
 * Shared between the Ops studio grid and the "Ops" menu in the Dev (code)
 * workbench navbar, so both surfaces expose exactly the same actions.
 */

export type OpsTx = (key: string, fallback: string) => string;

export type OpsCard = {
  id: string;
  label: string;
  description: string;
  prompt: string;
};

export type OpsGroup = {
  id: string;
  label: string;
  cards: OpsCard[];
};

export const OPS_COMMAND = "/ops";

export function opsGroups(tx: OpsTx): OpsGroup[] {
  const card = (id: string, label: string, description: string, prompt: string): OpsCard => ({
    id,
    label: tx(`studio.cards.${id}.label`, label),
    description: tx(`studio.cards.${id}.desc`, description),
    prompt,
  });
  return [
    {
      id: "containers",
      label: tx("studio.groups.containers", "Kubernetes & containers"),
      cards: [
        card(
          "opsClusterHealth",
          "Cluster health check",
          "Full Kubernetes audit: workloads, events, capacity.",
          "Run a full Kubernetes cluster health check: confirm the kubectl context first, then inspect nodes, unhealthy pods (CrashLoop, Pending, ImagePull, OOM), recent warning events, resource pressure, and misconfigured probes or limits. Use k8sgpt if available. Read-only. Deliver findings ordered by severity with the exact fix per issue.",
        ),
        card(
          "opsDebugWorkload",
          "Debug a workload",
          "Diagnose a failing pod, deployment or service.",
          "Debug the given Kubernetes workload: correlate describe output, container logs (current and previous), events, service/ingress wiring, and config/secret mounts to find why it fails. State the root cause with evidence, then propose the fix. Apply mutations only after explicit approval.",
        ),
        card(
          "opsDeployK8s",
          "Deploy to Kubernetes",
          "Manifests or Helm chart, applied and verified.",
          "Deploy the application to Kubernetes: write production-grade manifests (or a Helm chart) with resource limits, probes, security context and labels, validate with dry-run, apply to the confirmed non-prod target, then watch the rollout and verify health. Prefer the GitOps path (commit + PR) when the cluster is ArgoCD-managed.",
        ),
        card(
          "opsDockerize",
          "Containerize an app",
          "Dockerfile, compose file, build and run locally.",
          "Containerize the project: write an optimized multi-stage Dockerfile (small final image, non-root user, healthcheck), a docker-compose.yml for local dev with its dependencies, build the image, run it, and verify the app answers. Report image size and the commands to use.",
        ),
      ],
    },
    {
      id: "delivery",
      label: tx("studio.groups.delivery", "CI/CD & GitOps"),
      cards: [
        card(
          "opsCicdPipeline",
          "CI/CD pipeline",
          "Build, test, scan, publish - GitLab CI or GitHub Actions.",
          "Build a complete CI/CD pipeline for this repository (GitLab CI or GitHub Actions, matching the remote): lint, tests, security scan, container build and push with proper tagging and caching, then a deploy stage per environment with a manual gate for prod. Write the pipeline files and explain each stage.",
        ),
        card(
          "opsGitopsSetup",
          "GitOps with ArgoCD",
          "Wire a repo to ArgoCD: app, sync policy, environments.",
          "Set up GitOps for the project with ArgoCD: structure the deployment repo (base + overlays or Helm values per environment), write the ArgoCD Application manifests with a safe sync policy, and document the flow: git change, PR, review, auto-sync, verification. Use the ArgoCD MCP/CLI to create the app only after approval.",
        ),
        card(
          "opsArgoStatus",
          "Deployment status & drift",
          "ArgoCD apps: sync state, diff, history, drift.",
          "Inspect the ArgoCD deployments: list applications with sync and health status, run a diff between desired (git) and live state for the ones that drift, review recent sync history and failures, and recommend for each drifted app whether to sync (git wins) or port the live change back to git.",
        ),
        card(
          "opsHelmRelease",
          "Helm chart & release",
          "Author, lint, template, upgrade with rollback safety.",
          "Handle the Helm work: author or update the chart (values, templates, lint), render it with helm template and review the output, then upgrade with --atomic on the confirmed target and verify the release. Pin versions and report the exact chart version and overrides.",
        ),
      ],
    },
    {
      id: "cloud",
      label: tx("studio.groups.cloud", "Cloud"),
      cards: [
        card(
          "opsCloudAudit",
          "Cloud account audit",
          "Inventory, security posture, waste - AWS/Azure/GCP.",
          "Audit the cloud account (AWS, Azure or GCP - confirm account/subscription/project first): inventory the main resources, flag security issues (public buckets, open security groups, stale IAM users/keys, missing encryption), and list idle or oversized resources. Read-only. Deliver a report ordered by risk with remediation per finding.",
        ),
        card(
          "opsProvisionInfra",
          "Provision with Terraform",
          "VM, network, cluster - as code, planned before applied.",
          "Provision the requested infrastructure as code with Terraform: write the modules (network, compute, cluster, IAM as needed) with variables per environment and remote state, run terraform plan and walk through the diff. Apply only after explicit approval, then verify the resources exist and output the connection details.",
        ),
        card(
          "opsCloudCosts",
          "Cost analysis",
          "Where the money goes and how to reduce it.",
          "Analyze cloud costs: pull cost data per service and per resource where available, identify the top spenders and anomalies vs previous periods, and propose an optimization plan (rightsizing, reserved capacity, storage classes, cleanup of idle resources) with the estimated monthly saving per action.",
        ),
        card(
          "opsIamReview",
          "IAM & access review",
          "Who can do what - least-privilege gap analysis.",
          "Review IAM on the cloud account: map users, roles, service accounts and their policies, flag over-privileged identities, wildcard permissions, unused credentials and missing MFA, and propose a least-privilege remediation plan. Read-only: never change IAM without explicit approval.",
        ),
      ],
    },
    {
      id: "incidents",
      label: tx("studio.groups.incidents", "Incidents & observability"),
      cards: [
        card(
          "opsIncidentRca",
          "Incident diagnosis (RCA)",
          "Logs, metrics, events - root cause with evidence.",
          "Handle the incident like an SRE: scope the impact and pin the first bad timestamp, correlate recent deploys, logs, events, metrics and dependencies to find the root cause, and state it with the evidence chain. Propose immediate mitigation and the durable fix separately; apply mitigation only after approval, verify recovery, then write a short postmortem under ops/incidents/.",
        ),
        card(
          "opsObservability",
          "Observability setup",
          "Metrics, logs, alerts - Prometheus/Grafana stack.",
          "Set up observability for the target system: metrics collection (Prometheus or cloud-native), log aggregation, and Grafana dashboards for the golden signals (latency, traffic, errors, saturation), plus alert rules with sensible thresholds and runbook links. Deliver manifests/config as code and verify data flows in.",
        ),
        card(
          "opsK8sgptScan",
          "AI cluster diagnostic",
          "k8sgpt-style scan: every issue explained with a fix.",
          "Run an AI-assisted diagnostic of the Kubernetes cluster (use k8sgpt if installed, otherwise reproduce its analyzers manually): scan pods, services, ingress, PVCs, HPAs, nodes and RBAC for problems, explain each finding in plain language, and give the exact commands to fix each one. Read-only.",
        ),
        card(
          "opsRunbook",
          "Runbook / postmortem",
          "Operational doc: procedure, checks, rollback.",
          "Write the operational document requested: either a runbook for a recurring procedure (steps, pre-checks, commands, verification, rollback, escalation) or a postmortem for a past incident (timeline, root cause, impact, actions). Ground it in the real system by inspecting the actual configs and save it under ops/.",
        ),
      ],
    },
    {
      id: "servers",
      label: tx("studio.groups.servers", "Servers & network"),
      cards: [
        card(
          "opsServerHealth",
          "Server health check",
          "Load, disk, memory, services, security basics.",
          "Run a full health check of the server: load, memory, disk usage and inode pressure, failed systemd units, recent journal errors, listening ports, certificate expiry on TLS services, pending security updates, and suspicious logins. Read-only whitelist commands. Deliver findings ordered by urgency with the fix per item.",
        ),
        card(
          "opsServiceDebug",
          "Debug a service",
          "A daemon fails or misbehaves - find out why.",
          "Diagnose the failing service: status and recent journal, config validity, port conflicts, dependencies, resource limits, and recent package or config changes. State the root cause, then fix with the smallest action (reload before restart before reboot) and only with approval. Keep a .bak of any config touched and verify the symptom is gone.",
        ),
        card(
          "opsNetworkDiag",
          "Network diagnostic",
          "DNS, routes, ports, firewall, TLS - end to end.",
          "Diagnose the network path end to end: DNS resolution, routing, reachability (ping/traceroute), listening ports and firewall rules on both ends, TLS handshake and certificate validity. Locate exactly where the path breaks, prove it, and propose the fix. Firewall changes require explicit approval.",
        ),
        card(
          "opsHardenBackup",
          "Hardening & backups",
          "Patch plan, hardening checklist, backup strategy.",
          "Assess and improve the server's resilience: pending patches and reboot requirements, hardening quick wins (SSH config, firewall posture, unused services, file permissions), and the backup situation - what is backed up, where, and whether restore is tested. Deliver a prioritized plan; apply changes only with approval.",
        ),
      ],
    },
  ];
}

/**
 * Builds the text seeded into the chat composer for an ops action.
 * The trailing blank line invites the user to add their context before sending.
 */
export function opsSeedText(card: OpsCard): string {
  return `${OPS_COMMAND} ${card.prompt}\n\n`;
}
