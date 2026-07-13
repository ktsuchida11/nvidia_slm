# SSMセッションの監査ログ設定（mra_terraform modules/bastion/ssm.tf 踏襲）
resource "aws_cloudwatch_log_group" "ssm_session" {
  name              = "/aws/ssm/${local.prefix}-session"
  retention_in_days = var.ssm_session_retention_in_days
}

# AWS予約名 SSM-SessionManagerRunShell はTerraformで管理できないためカスタム名で作成。
# 接続: aws ssm start-session --target <instance-id> --document-name <この名前>
resource "aws_ssm_document" "session_manager_run_shell" {
  name            = "${local.prefix}-SessionManagerRunShell"
  document_type   = "Session"
  document_format = "JSON"

  content = jsonencode({
    schemaVersion = "1.0"
    description   = "Session Manager settings with CloudWatch Logs audit trail"
    sessionType   = "Standard_Stream"
    inputs = {
      cloudWatchLogGroupName      = aws_cloudwatch_log_group.ssm_session.name
      cloudWatchEncryptionEnabled = false
      cloudWatchStreamingEnabled  = true
      idleSessionTimeout          = "20"
      runAsEnabled                = false
    }
  })
}
