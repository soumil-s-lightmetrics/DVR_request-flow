#!/usr/bin/env bash
set -e
set -o pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Constants
IMAGE_NAME="chat-knowledge-base"
ECR_REPOSITORY="chat-knowledge-base"

# Parse arguments
ENVIRONMENT=""
DRY_RUN=false
FORCE=false

function print_usage() {
  echo "Usage: $0 <ENVIRONMENT> [OPTIONS]"
  echo ""
  echo "Arguments:"
  echo "  ENVIRONMENT    Required. Either 'DEV' or 'PROD'"
  echo ""
  echo "Options:"
  echo "  --dry-run      Validate configuration without building or pushing"
  echo "  --force        Override safety checks (not recommended for PROD)"
  echo ""
  echo "Examples:"
  echo "  $0 DEV"
  echo "  $0 PROD"
  echo "  $0 DEV --dry-run"
}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
  case $1 in
    DEV|PROD)
      ENVIRONMENT="$1"
      shift
      ;;
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    --force)
      FORCE=true
      shift
      ;;
    -h|--help)
      print_usage
      exit 0
      ;;
    *)
      echo -e "${RED}Error: Unknown argument '$1'${NC}"
      print_usage
      exit 1
      ;;
  esac
done

# Validate environment argument
if [ -z "$ENVIRONMENT" ]; then
  echo -e "${RED}Error: ENVIRONMENT argument is required${NC}"
  print_usage
  exit 1
fi

# Set environment-specific configuration
case $ENVIRONMENT in
  DEV)
    AWS_ACCOUNT="443314737660"
    AWS_REGION="ap-south-1"
    EKS_CLUSTER="dt1-dev-aps1"
    K8S_NAMESPACE="llm-kb"
    ;;
  PROD)
    AWS_ACCOUNT="475421081887"
    AWS_REGION="us-west-2"
    EKS_CLUSTER="prod-usw2"
    K8S_NAMESPACE="llm-kb"
    ;;
  *)
    echo -e "${RED}Error: Invalid environment '$ENVIRONMENT'. Must be DEV or PROD${NC}"
    exit 1
    ;;
esac

ECR_URI="${AWS_ACCOUNT}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPOSITORY}"

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}Deploying to ${ENVIRONMENT} Environment${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""
echo "AWS Account: ${AWS_ACCOUNT}"
echo "AWS Region: ${AWS_REGION}"
echo "EKS Cluster: ${EKS_CLUSTER}"
echo "ECR Repository: ${ECR_REPOSITORY}"
echo "Dry Run: ${DRY_RUN}"
echo ""

# Check required tools
function check_tool() {
  if ! command -v $1 &> /dev/null; then
    echo -e "${RED}Error: $1 is not installed${NC}"
    echo "Please install $1 and try again"
    exit 1
  fi
}

echo -e "${BLUE}Checking prerequisites...${NC}"
check_tool aws
check_tool docker
check_tool git

# Check Docker daemon
if ! docker info &> /dev/null; then
  echo -e "${RED}Error: Docker daemon is not running${NC}"
  echo "Please start Docker and try again"
  exit 1
fi

echo -e "${GREEN}✓ All prerequisites met${NC}"
echo ""

# Validate AWS credentials
echo -e "${BLUE}Validating AWS credentials...${NC}"
if ! AWS_IDENTITY=$(aws sts get-caller-identity 2>&1); then
  echo -e "${RED}Error: AWS credentials not configured${NC}"
  echo ""
  echo "Please configure AWS credentials using one of:"
  echo "  1. aws configure"
  echo "  2. Export AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY"
  echo "  3. Use AWS SSO: aws sso login"
  exit 1
fi

CURRENT_ACCOUNT=$(echo "$AWS_IDENTITY" | grep -o '"Account": "[^"]*"' | cut -d'"' -f4)
if [ "$CURRENT_ACCOUNT" != "$AWS_ACCOUNT" ]; then
  echo -e "${RED}Error: AWS account mismatch${NC}"
  echo "Expected account: ${AWS_ACCOUNT} (${ENVIRONMENT})"
  echo "Current account: ${CURRENT_ACCOUNT}"
  echo ""
  echo "Please switch to the correct AWS account/profile"
  exit 1
fi

echo -e "${GREEN}✓ AWS credentials valid for account ${AWS_ACCOUNT}${NC}"
echo ""

# Check git status
echo -e "${BLUE}Checking git repository status...${NC}"
if ! git rev-parse --git-dir &> /dev/null; then
  echo -e "${RED}Error: Not in a git repository${NC}"
  exit 1
fi

# Check for uncommitted changes
GIT_STATUS=$(git status --porcelain)
if [ -n "$GIT_STATUS" ]; then
  echo -e "${YELLOW}⚠ Warning: Working tree has uncommitted changes${NC}"

  if [ "$ENVIRONMENT" = "PROD" ] && [ "$FORCE" = false ]; then
    echo -e "${RED}Error: Cannot deploy to PROD with uncommitted changes${NC}"
    echo ""
    echo "Please commit your changes or use --force to override (not recommended)"
    echo ""
    echo "Uncommitted changes:"
    echo "$GIT_STATUS"
    exit 1
  fi

  echo "Version will be tagged with -unstable suffix"
  UNSTABLE_SUFFIX="-unstable"
else
  echo -e "${GREEN}✓ Working tree is clean${NC}"
  UNSTABLE_SUFFIX=""
fi
echo ""

# Read version from VERSION file
if [ ! -f "VERSION" ]; then
  echo -e "${RED}Error: VERSION file not found${NC}"
  echo "Please create a VERSION file with semantic version (e.g., 1.2.4)"
  exit 1
fi

BASE_VERSION=$(cat VERSION | tr -d '[:space:]')
if ! echo "$BASE_VERSION" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.]+)?$'; then
  echo -e "${RED}Error: Invalid version format in VERSION file: ${BASE_VERSION}${NC}"
  echo "Version must be in format: major.minor.patch (e.g., 1.2.4) with an optional prerelease suffix (e.g., 1.2.4-prerelease)"
  exit 1
fi

IMAGE_VERSION="${BASE_VERSION}${UNSTABLE_SUFFIX}"

echo -e "${BLUE}Version Information:${NC}"
echo "Base Version: ${BASE_VERSION}"
echo "Image Version: ${IMAGE_VERSION}"
echo "Git Commit: $(git rev-parse --short HEAD)"
echo ""

# Exit if dry-run
if [ "$DRY_RUN" = true ]; then
  echo -e "${GREEN}========================================${NC}"
  echo -e "${GREEN}Dry Run Complete${NC}"
  echo -e "${GREEN}========================================${NC}"
  echo ""
  echo "Would build: ${IMAGE_NAME}:${IMAGE_VERSION}"
  echo "Would push to: ${ECR_URI}:${IMAGE_VERSION}"
  echo "Tag: ${IMAGE_VERSION}"
  echo ""
  echo -e "${GREEN}All validations passed. Ready to deploy.${NC}"
  exit 0
fi

# Build and test Docker image using package.sh
echo -e "${BLUE}Building and testing Docker image using package.sh...${NC}"
echo "Image: ${IMAGE_NAME}:${IMAGE_VERSION}"
echo ""

if ! IMAGE_VERSION="${IMAGE_VERSION}" NON_INTERACTIVE=true ./package.sh; then
  echo -e "${RED}Error: Docker build or test failed${NC}"
  exit 1
fi

echo ""
echo -e "${GREEN}✓ Docker image built and tested successfully${NC}"
echo ""

# Login to ECR
echo -e "${BLUE}Logging in to ECR...${NC}"
if ! aws ecr get-login-password --region "${AWS_REGION}" | \
     docker login --username AWS --password-stdin "${AWS_ACCOUNT}.dkr.ecr.${AWS_REGION}.amazonaws.com" &> /dev/null; then
  echo -e "${RED}Error: ECR login failed${NC}"
  echo ""
  echo "Please ensure you have the following IAM permissions:"
  echo "  - ecr:GetAuthorizationToken"
  echo "  - ecr:BatchCheckLayerAvailability"
  echo "  - ecr:PutImage"
  echo "  - ecr:InitiateLayerUpload"
  echo "  - ecr:UploadLayerPart"
  echo "  - ecr:CompleteLayerUpload"
  exit 1
fi

echo -e "${GREEN}✓ Logged in to ECR${NC}"
echo ""

# Tag image for ECR
echo -e "${BLUE}Tagging image for ECR...${NC}"
docker tag "${IMAGE_NAME}:${IMAGE_VERSION}" "${ECR_URI}:${IMAGE_VERSION}"

echo -e "${GREEN}✓ Image tagged${NC}"
echo ""

# Push to ECR
echo -e "${BLUE}Pushing image to ECR...${NC}"
echo "This may take a few minutes..."
echo ""

if ! docker push "${ECR_URI}:${IMAGE_VERSION}"; then
  echo -e "${RED}Error: Failed to push image${NC}"
  echo ""
  echo "Common issues:"
  echo "  1. ECR repository '${ECR_REPOSITORY}' does not exist in account ${AWS_ACCOUNT}"
  echo "  2. Insufficient IAM permissions for ECR push"
  echo "  3. Network connectivity issues"
  echo ""
  echo "To create the ECR repository:"
  echo "  aws ecr create-repository --repository-name ${ECR_REPOSITORY} --region ${AWS_REGION}"
  exit 1
fi

echo ""
echo -e "${GREEN}✓ Image pushed successfully${NC}"
echo ""

# Success output
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}Deployment to ECR Complete!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo -e "${BLUE}Image Details:${NC}"
echo "  Local Image: ${IMAGE_NAME}:${IMAGE_VERSION}"
echo "  ECR Image: ${ECR_URI}:${IMAGE_VERSION}"
echo "  Tag Pushed: ${IMAGE_VERSION}"
echo ""
echo -e "${YELLOW}========================================${NC}"
echo -e "${YELLOW}Next Steps - Deploy to Kubernetes${NC}"
echo -e "${YELLOW}========================================${NC}"
echo ""
echo "1. Update the deployment with the new image:"
echo ""
echo -e "${BLUE}kubectl set image deployment/chat-knowledge-base \\${NC}"
echo -e "${BLUE}  chat-knowledge-base=${ECR_URI}:${IMAGE_VERSION} \\${NC}"
echo -e "${BLUE}  -n ${K8S_NAMESPACE}${NC}"
echo ""
echo "2. Monitor the rollout:"
echo ""
echo -e "${BLUE}kubectl rollout status deployment/chat-knowledge-base -n ${K8S_NAMESPACE}${NC}"
echo ""
echo "3. Verify the deployment:"
echo ""
echo -e "${BLUE}kubectl get pods -n ${K8S_NAMESPACE} -l app=chat-knowledge-base${NC}"
echo ""
echo -e "${GREEN}Deployment process complete!${NC}"
