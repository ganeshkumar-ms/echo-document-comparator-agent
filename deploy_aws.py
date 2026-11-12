import io
import json
import mimetypes
import os
import subprocess
import sys
import time
import zipfile
from pathlib import Path

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

import boto3
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / 'backend' / '.env')
load_dotenv()

REGION = 'us-east-1'
sts = boto3.client('sts', region_name=REGION)
ACCOUNT_ID = sts.get_caller_identity()['Account']
BUCKET_NAME = f'echo-ui-{ACCOUNT_ID}'
ROLE_NAME = 'echo-backend-lambda-role'
LAMBDA_NAME = 'echo-backend-api'
API_NAME = 'echo-http-api'

s3 = boto3.client('s3', region_name=REGION)
iam = boto3.client('iam', region_name=REGION)
lam = boto3.client('lambda', region_name=REGION)
apigw = boto3.client('apigatewayv2', region_name=REGION)
cf = boto3.client('cloudfront', region_name=REGION)


def step0_build_frontend(api_endpoint: str = ''):
    fe_dir = BASE_DIR / 'frontend'
    if api_endpoint:
        (fe_dir / '.env.production').write_text(f'VITE_API_BASE={api_endpoint}\n', encoding='utf-8')
    if not (fe_dir / 'node_modules').exists():
        print('0. Installing frontend dependencies...')
        subprocess.run(['cmd', '/c', 'npm install'], cwd=str(fe_dir), check=True)
    print('0. Building Vite frontend bundle...')
    subprocess.run(['cmd', '/c', 'npm run build'], cwd=str(fe_dir), check=True)


def step1_s3_and_upload():
    print(f'1. Ensuring S3 bucket {BUCKET_NAME} exists...')
    try:
        s3.head_bucket(Bucket=BUCKET_NAME)
    except Exception:
        s3.create_bucket(Bucket=BUCKET_NAME)

    s3.put_public_access_block(
        Bucket=BUCKET_NAME,
        PublicAccessBlockConfiguration={
            'BlockPublicAcls': True,
            'IgnorePublicAcls': True,
            'BlockPublicPolicy': True,
            'RestrictPublicBuckets': True,
        },
    )

    s3.put_bucket_cors(
        Bucket=BUCKET_NAME,
        CORSConfiguration={
            'CORSRules': [
                {
                    'AllowedHeaders': ['*'],
                    'AllowedMethods': ['GET', 'PUT', 'POST', 'HEAD'],
                    'AllowedOrigins': ['*'],
                    'ExposeHeaders': ['ETag'],
                    'MaxAgeSeconds': 3600,
                }
            ]
        },
    )

    dist_dir = BASE_DIR / 'frontend' / 'dist'
    if not dist_dir.exists():
        raise RuntimeError('Frontend dist folder missing!')

    for file_path in dist_dir.rglob('*'):
        if file_path.is_file():
            rel = file_path.relative_to(dist_dir).as_posix()
            ctype, _ = mimetypes.guess_type(str(file_path))
            if file_path.suffix == '.js':
                ctype = 'application/javascript'
            elif file_path.suffix == '.css':
                ctype = 'text/css'
            elif file_path.suffix == '.html':
                ctype = 'text/html; charset=utf-8'
            elif file_path.suffix == '.svg':
                ctype = 'image/svg+xml'
            kwargs = {
                'Bucket': BUCKET_NAME,
                'Key': rel,
                'Body': file_path.read_bytes(),
                'ContentType': ctype or 'application/octet-stream',
            }
            if file_path.name == 'index.html':
                kwargs['CacheControl'] = 'no-cache, no-store, must-revalidate'
            s3.put_object(**kwargs)
            print(f'   Uploaded s3://{BUCKET_NAME}/{rel} ({ctype})')


def step2_iam_role() -> str:
    print(f'2. Ensuring IAM Role {ROLE_NAME}...')
    trust = {
        'Version': '2012-10-17',
        'Statement': [{
            'Effect': 'Allow',
            'Principal': {'Service': 'lambda.amazonaws.com'},
            'Action': 'sts:AssumeRole',
        }],
    }

    try:
        role_arn = iam.get_role(RoleName=ROLE_NAME)['Role']['Arn']
    except iam.exceptions.NoSuchEntityException:
        role_arn = iam.create_role(
            RoleName=ROLE_NAME,
            AssumeRolePolicyDocument=json.dumps(trust),
        )['Role']['Arn']
        print('   Created IAM role, waiting 10s for propagation...')
        time.sleep(10)

    for p in [
        'arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole',
        'arn:aws:iam::aws:policy/AmazonBedrockFullAccess',
        'arn:aws:iam::aws:policy/AmazonS3FullAccess',
    ]:
        try:
            iam.attach_role_policy(RoleName=ROLE_NAME, PolicyArn=p)
        except Exception:
            pass
    return role_arn


def build_lambda_zip() -> bytes:
    print('3. Packaging Lambda deployment zip...')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        backend_dir = BASE_DIR / 'backend'
        for root, _, files in os.walk(backend_dir):
            if any(skip in root for skip in ['venv', '__pycache__', '.git', 'uploads']):
                continue
            for f in files:
                if f.endswith('.pyc') or f.endswith('.env'):
                    continue
                full_path = Path(root) / f
                rel_path = full_path.relative_to(backend_dir).as_posix()
                z.write(full_path, rel_path)
    return buf.getvalue()


def step3_lambda(role_arn: str) -> str:
    print(f'4. Deploying Lambda function {LAMBDA_NAME}...')
    zip_bytes = build_lambda_zip()
    env_vars = {
        'Variables': {
            'ECHO_DOCS_BUCKET': BUCKET_NAME,
        }
    }

    try:
        fn = lam.get_function(FunctionName=LAMBDA_NAME)
        lam.update_function_code(FunctionName=LAMBDA_NAME, ZipFile=zip_bytes)
        waiter = lam.get_waiter('function_updated_v2')
        waiter.wait(FunctionName=LAMBDA_NAME)
        lam.update_function_configuration(
            FunctionName=LAMBDA_NAME,
            Timeout=120,
            MemorySize=512,
            Environment=env_vars,
        )
        waiter.wait(FunctionName=LAMBDA_NAME)
        arn = fn['Configuration']['FunctionArn']
        print(f'   Updated existing Lambda: {arn}')
    except lam.exceptions.ResourceNotFoundException:
        fn = lam.create_function(
            FunctionName=LAMBDA_NAME,
            Runtime='python3.11',
            Role=role_arn,
            Handler='lambda_function.lambda_handler',
            Code={'ZipFile': zip_bytes},
            Timeout=120,
            MemorySize=512,
            Environment=env_vars,
        )
        arn = fn['FunctionArn']
        print(f'   Created Lambda: {arn}')
    return arn


def step4_api_gateway(lambda_arn: str) -> tuple:
    print(f'5. Configuring HTTP API Gateway {API_NAME}...')
    apis = apigw.get_apis()['Items']
    api_id = None
    for a in apis:
        if a['Name'] == API_NAME:
            api_id = a['ApiId']
            break

    if not api_id:
        res = apigw.create_api(
            Name=API_NAME,
            ProtocolType='HTTP',
            CorsConfiguration={
                'AllowOrigins': ['*'],
                'AllowMethods': ['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS'],
                'AllowHeaders': ['*'],
            },
        )
        api_id = res['ApiId']
        print(f'   Created API Gateway: {api_id}')

    int_uri = f'arn:aws:apigateway:{REGION}:lambda:path/2015-03-31/functions/{lambda_arn}/invocations'
    ints = apigw.get_integrations(ApiId=api_id)['Items']
    int_id = None
    for i in ints:
        if i.get('IntegrationUri') == int_uri:
            int_id = i['IntegrationId']
            break

    if not int_id:
        res = apigw.create_integration(
            ApiId=api_id,
            IntegrationType='AWS_PROXY',
            IntegrationUri=int_uri,
            PayloadFormatVersion='2.0',
        )
        int_id = res['IntegrationId']

    routes = apigw.get_routes(ApiId=api_id)['Items']
    route_keys = {r['RouteKey'] for r in routes}
    target = f'integrations/{int_id}'

    for rk in [
        'ANY /{proxy+}',
        'GET /api/health',
        'GET /health',
        'POST /api/get-upload-url',
        'GET /api/view-document',
        'POST /api/compare',
        'POST /api/chat',
        'POST /api/analyze',
        'POST /api/message',
    ]:
        if rk not in route_keys:
            try:
                apigw.create_route(ApiId=api_id, RouteKey=rk, Target=target)
            except Exception:
                pass

    try:
        apigw.get_stage(ApiId=api_id, StageName='$default')
    except apigw.exceptions.NotFoundException:
        apigw.create_stage(ApiId=api_id, StageName='$default', AutoDeploy=True)

    try:
        lam.add_permission(
            FunctionName=LAMBDA_NAME,
            StatementId=f'apigw-invoke-{api_id}',
            Action='lambda:InvokeFunction',
            Principal='apigateway.amazonaws.com',
            SourceArn=f'arn:aws:execute-api:{REGION}:{ACCOUNT_ID}:{api_id}/*/*',
        )
    except lam.exceptions.ResourceConflictException:
        pass

    api_endpoint = f'https://{api_id}.execute-api.{REGION}.amazonaws.com'
    print(f'   API Gateway ready: {api_endpoint}')
    return api_id, api_endpoint


def step5_cloudfront(api_id: str):
    print('6. Configuring CloudFront Distribution with OAC...')
    s3_origin_id = f'S3-{BUCKET_NAME}'
    s3_domain = f'{BUCKET_NAME}.s3.{REGION}.amazonaws.com'
    api_origin_id = f'API-{api_id}'
    api_domain = f'{api_id}.execute-api.{REGION}.amazonaws.com'

    oac_name = f'echo-oac-{ACCOUNT_ID}'
    oac_id = None
    try:
        for o in cf.list_origin_access_controls()['OriginAccessControlList'].get('Items', []):
            if o['Name'] == oac_name:
                oac_id = o['Id']
                break
    except Exception:
        pass

    if not oac_id:
        res = cf.create_origin_access_control(
            OriginAccessControlConfig={
                'Name': oac_name,
                'Description': 'OAC for ECHO S3 Bucket',
                'SigningProtocol': 'sigv4',
                'SigningBehavior': 'always',
                'OriginAccessControlOriginType': 's3',
            }
        )
        oac_id = res['OriginAccessControl']['Id']
        print(f'   Created OAC: {oac_id}')

    dist_id = None
    dist_domain = None

    for d in cf.list_distributions().get('DistributionList', {}).get('Items', []):
        for orig in d.get('Origins', {}).get('Items', []):
            if orig.get('DomainName') == s3_domain or orig.get('DomainName') == api_domain or d.get('Comment') == 'ECHO - Document Comparator':
                dist_id = d['Id']
                dist_domain = d['DomainName']
                break
        if dist_id:
            break

    dist_payload = {
        'CallerReference': str(time.time()),
        'Comment': 'ECHO - Document Comparator',
        'Enabled': True,
        'DefaultRootObject': 'index.html',
        'Origins': {
            'Quantity': 2,
            'Items': [
                {
                    'Id': s3_origin_id,
                    'DomainName': s3_domain,
                    'OriginAccessControlId': oac_id,
                    'S3OriginConfig': {'OriginAccessIdentity': ''},
                },
                {
                    'Id': api_origin_id,
                    'DomainName': api_domain,
                    'CustomOriginConfig': {
                        'HTTPPort': 80,
                        'HTTPSPort': 443,
                        'OriginProtocolPolicy': 'https-only',
                        'OriginSslProtocols': {'Quantity': 1, 'Items': ['TLSv1.2']},
                        'OriginReadTimeout': 120,
                        'OriginKeepaliveTimeout': 5,
                    },
                },
            ],
        },
        'DefaultCacheBehavior': {
            'TargetOriginId': s3_origin_id,
            'ViewerProtocolPolicy': 'redirect-to-https',
            'AllowedMethods': {
                'Quantity': 2,
                'Items': ['GET', 'HEAD'],
                'CachedMethods': {'Quantity': 2, 'Items': ['GET', 'HEAD']},
            },
            'ForwardedValues': {
                'QueryString': False,
                'Cookies': {'Forward': 'none'},
            },
            'MinTTL': 0,
            'DefaultTTL': 3600,
            'MaxTTL': 86400,
            'Compress': True,
        },
        'CacheBehaviors': {
            'Quantity': 1,
            'Items': [
                {
                    'PathPattern': '/api/*',
                    'TargetOriginId': api_origin_id,
                    'TrustedSigners': {'Enabled': False, 'Quantity': 0},
                    'TrustedKeyGroups': {'Enabled': False, 'Quantity': 0},
                    'ViewerProtocolPolicy': 'redirect-to-https',
                    'AllowedMethods': {
                        'Quantity': 7,
                        'Items': ['HEAD', 'DELETE', 'POST', 'GET', 'OPTIONS', 'PUT', 'PATCH'],
                        'CachedMethods': {'Quantity': 2, 'Items': ['HEAD', 'GET']},
                    },
                    'SmoothStreaming': False,
                    'Compress': True,
                    'LambdaFunctionAssociations': {'Quantity': 0},
                    'FunctionAssociations': {'Quantity': 0},
                    'FieldLevelEncryptionId': '',
                    'CachePolicyId': '4135ea2d-6df8-44a3-9df3-4b5a84be39ad',
                    'OriginRequestPolicyId': 'b689b0a8-53d0-40ab-baf2-68738e2966ac',
                    'GrpcConfig': {'Enabled': False},
                }
            ],
        },
        'CustomErrorResponses': {
            'Quantity': 1,
            'Items': [
                {
                    'ErrorCode': 403,
                    'ResponsePagePath': '/index.html',
                    'ResponseCode': '200',
                    'ErrorCachingMinTTL': 0,
                }
            ],
        },
    }

    if dist_id:
        print(f'   CloudFront distribution exists: {dist_id} ({dist_domain})')
    else:
        print('   Creating new CloudFront Distribution...')
        res = cf.create_distribution(DistributionConfig=dist_payload)
        dist_id = res['Distribution']['Id']
        dist_domain = res['Distribution']['DomainName']
        print(f'   Created Distribution: {dist_id} ({dist_domain})')

    bucket_policy = {
        'Version': '2012-10-17',
        'Statement': [
            {
                'Sid': 'AllowCloudFrontServicePrincipalReadOnly',
                'Effect': 'Allow',
                'Principal': {'Service': 'cloudfront.amazonaws.com'},
                'Action': 's3:GetObject',
                'Resource': f'arn:aws:s3:::{BUCKET_NAME}/*',
                'Condition': {
                    'StringEquals': {
                        'AWS:SourceArn': f'arn:aws:cloudfront::{ACCOUNT_ID}:distribution/{dist_id}'
                    }
                },
            }
        ],
    }
    s3.put_bucket_policy(Bucket=BUCKET_NAME, Policy=json.dumps(bucket_policy))

    print('   Invalidating CloudFront cache...')
    cf.create_invalidation(
        DistributionId=dist_id,
        InvalidationBatch={
            'Paths': {'Quantity': 1, 'Items': ['/*']},
            'CallerReference': str(time.time()),
        },
    )

    return dist_id, dist_domain


def main():
    print('====================================================')
    print('   ECHO DEPLOYMENT TO AWS (Nova Pro + S3 + CF)     ')
    print('====================================================')
    role_arn = step2_iam_role()
    lambda_arn = step3_lambda(role_arn)
    api_id, api_endpoint = step4_api_gateway(lambda_arn)
    step0_build_frontend(api_endpoint)
    step1_s3_and_upload()
    dist_id, dist_domain = step5_cloudfront(api_id)

    print('\n====================================================')
    print('   ECHO DEPLOYMENT COMPLETED SUCCESSFULLY!')
    print(f'   CloudFront URL: https://{dist_domain}')
    print(f'   API Gateway:   {api_endpoint}')
    print(f'   Distribution:  {dist_id}')
    print('====================================================\n')


if __name__ == '__main__':
    main()

