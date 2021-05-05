#!/bin/bash
export KUBECONFIG=/etc/kubernetes/admin.conf
kubectl delete node $1