#!/bin/bash

# Load environment variables
if [ -f .env ]; then
    export $(cat .env | grep -v '#' | awk '/=/ {print $1}')
else
    echo "Error: .env file not found"
    exit 1
fi


IMAGE_NAME="ycy10/grade-system"
TAG="latest"

echo "Building Docker image: $IMAGE_NAME:$TAG"
docker build -t $IMAGE_NAME:$TAG .

echo "Pushing Docker image to Docker Hub..."
docker push $IMAGE_NAME:$TAG

echo "Done! Image published to $IMAGE_NAME:$TAG"
