# SSMセッションの監査設定（bastionの有無に依存しないためルートに配置。
# ログはセッション対象インスタンス側のエージェントが書くため、
# bastion/gpu_host 両モジュールのIAMロールに書込権限を渡す）
resource "aws_cloudwatch_log_group" "ssm_session" {
  name              = "/aws/ssm/${var.project}-${var.environment}-session"
  retention_in_days = var.ssm_session_retention_in_days
}

# AWS予約名 SSM-SessionManagerRunShell はTerraformで管理できないためカスタム名で作成。
# 接続: aws ssm start-session --target <instance-id> --document-name <この名前>
resource "aws_ssm_document" "session_manager_run_shell" {
  name            = "${var.project}-${var.environment}-SessionManagerRunShell"
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
      idleSessionTimeout          = "60"   # 上限値。学習の待ち時間で切れないように(ループ8)。長時間ジョブ自体はtmux必須
      runAsEnabled                = false
    }
  })
}
