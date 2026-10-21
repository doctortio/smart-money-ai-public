# Central de Sinais — homologação

O módulo de política (`telegram_worker/signal_center_runtime.py`) valida exatamente dois ativos, `5m`, horários aprovados e parada de emergência. Os caminhos automático e manual são independentes, mas a parada de emergência bloqueia ambos.

A configuração persistente e o histórico dependem das migrations `20261022110000_signal_center_admin.sql` e `20261022113000_signal_center_admin_rpc.sql`. A integração remota do worker deve usar uma função intermediária HMAC; não se deve distribuir `service_role` ao GitHub, navegador ou Telegram worker.

Homologação: aplicar migrations, publicar a função intermediária, configurar apenas os secrets do gateway e executar prévias manuais sem envio. Rollback: desativar a configuração, interromper o workflow e reverter apenas os objetos adicionados após validação.
