output "bastion_instance_id" { value = aws_instance.bastion.id }
output "bastion_security_group_id" { value = aws_security_group.bastion.id }
output "session_document_name" { value = aws_ssm_document.session_manager_run_shell.name }
output "session_log_group_name" { value = aws_cloudwatch_log_group.ssm_session.name }
