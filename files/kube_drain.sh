#!/bin/bash
export KUBECONFIG=/etc/kubernetes/admin.conf
kubectl drain --ignore-daemonsets $1