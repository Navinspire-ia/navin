---
name: kubernetes-platform
description: Build and operate Kubernetes platforms - EKS, GKE, AKS, OpenShift, K3s, RKE2, MicroK8s - with ingress, cert-manager, external-dns, autoscaling, storage classes, CRDs/operators and version upgrades. Use for cluster creation, platform add-ons and upgrades; use kubernetes-operator for workload debugging.
metadata: {"navin":{"emoji":"🛳️","category":"devops"}}
---

# Kubernetes Platform

## Overview

You own the cluster as a product: provisioning, the add-on baseline, capacity, upgrades and the contract offered to application teams. Every command goes through the `infra` engine.

## When to use

- Create a cluster (managed or self-hosted), add a node pool, install the platform baseline.
- Upgrade control plane, nodes and add-ons.
- Install or manage operators and CRDs.

## Engine

1. `infra action=status` - kube contexts and which are production.
2. `infra action=discover provider=kubernetes context=<ctx>` (CRDs included) plus the cloud provider and `provider=helm`.
3. `infra action=graph op=build sources=[kubernetes,aws]` then `op=impact node=<namespace|nodepool>` before draining or upgrading.
4. `infra action=check` on every kubectl/helm/eksctl/gcloud/az command; `infra action=access` for `kubectl auth can-i`.
5. `infra action=plan` for upgrades and baseline installs; `infra action=run` then `infra action=verify`.

## Workflow

1. **Provision** - managed: `eksctl`/Terraform (EKS), `gcloud container clusters create` (GKE, regional), `az aks create` (AKS, zones 1 2 3); self-hosted: K3s, RKE2, MicroK8s with HA control plane (3 servers, embedded etcd).
2. **Baseline** - ingress controller (ingress-nginx, AWS LB Controller, Traefik, Gateway API), cert-manager with ClusterIssuer, external-dns scoped to a zone, metrics-server, Cluster Autoscaler or Karpenter, HPA/VPA, default StorageClass (gp3, pd-balanced, managed-csi, Longhorn), CSI snapshot controller, NetworkPolicies default-deny, PodSecurity admission, observability stack.
3. **Tenancy** - namespaces, ResourceQuota, LimitRange, RBAC per team.
4. **Operators** - install via Helm or OLM, pin versions, check CRD upgrade notes.
5. **Upgrades** - one minor at a time: check deprecated APIs (`kubent`, `pluto`), add-on compatibility, PodDisruptionBudgets; control plane, then node pools (surge), then add-ons.

## Command families

```bash
kubectl config get-contexts; kubectl version; kubectl get nodes -o wide
kubectl api-resources; kubectl get crd
kubectl get sc; kubectl get pdb -A; kubectl top nodes
kubectl diff -f manifests/; kubectl apply --dry-run=server -f manifests/
kubectl cordon <n>; kubectl drain <n> --ignore-daemonsets --delete-emptydir-data
eksctl get cluster; eksctl upgrade cluster --name <c> --approve
aws eks describe-addon-versions --kubernetes-version 1.30
gcloud container clusters upgrade <c> --master --cluster-version <v> --region <r>
az aks get-upgrades -g <rg> -n <c>; az aks upgrade -g <rg> -n <c> --kubernetes-version <v>
k3s kubectl get nodes; rke2 --version; microk8s status
helm upgrade --install cert-manager jetstack/cert-manager -n cert-manager --set crds.enabled=true
```

## Safety rules

- Upgrades, drains, node pool deletion, CRD deletion and changes on production contexts are critical: user validation.
- Always `kubectl diff` / `--dry-run=server` / `helm diff` before apply.
- Never delete a CRD without listing its custom resources first (deleting it deletes them).

## Verification and rollback

- Verify: all nodes Ready at the target version, `kubectl get pods -A` no CrashLoop, certificates Ready, ingress answers over TLS, autoscaler logs clean.
- Rollback: managed control planes cannot downgrade - mitigate with a new node pool on the previous version and workload rollback; `helm rollback` for add-ons; etcd snapshot restore for self-hosted.

## Deliverable

Cluster inventory (versions, node pools, add-ons), changes applied with run ids, verification evidence, upgrade notes, rollback path.
