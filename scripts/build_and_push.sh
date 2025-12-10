#!/bin/bash
set -e

IMAGE_NAME="get2kamal/jmeter-custom"
TAG=${1:-latest}

echo "Building Docker image..."
docker build -t ${IMAGE_NAME}:${TAG} -f ../docker/Dockerfile ../

echo "Pushing to Docker Hub..."
docker push ${IMAGE_NAME}:${TAG}

echo "DONE: ${IMAGE_NAME}:${TAG}"
