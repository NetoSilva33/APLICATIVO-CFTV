# Segura Gestão — Segurança eletrônica

Aplicativo web responsivo e instalável como PWA, pensado para operação de empresas de segurança eletrônica em computador e celular. O servidor atual usa SQLite local e a biblioteca padrão do Python; não precisa de pacotes externos.

## Executar

Instale Python 3.10 ou superior e, nesta pasta, execute:

```bash
python3 server.py
```

Abra http://127.0.0.1:8000. No Windows, execute `py -3 server.py` ou abra `iniciar.bat`. Na primeira abertura, crie o acesso de administrador com uma senha de pelo menos 12 caracteres. O administrador cadastra os outros acessos na área **Acessos**.

O servidor escuta somente em `127.0.0.1` por padrão. Assim, esta execução serve ao próprio computador. Para acessar de outros aparelhos, hospede a aplicação atrás de HTTPS e configure o proxy antes de disponibilizar o endereço à equipe. Crie o administrador antes de expor uma instalação nova. Não exponha o servidor diretamente à internet.

## Áreas incluídas

- **Ordens de serviço:** abertura por cliente e equipamento, registro do serviço e materiais utilizados, situação do atendimento, assinatura desenhada com mouse ou tela sensível ao toque e impressão do documento em PDF pelo navegador.
- **Clientes e equipamentos:** cadastro de clientes e equipamentos vinculados, fabricante, modelo, número de série ou local.
- **Manutenção:** registro de visita, histórico por equipamento e datas da última e próxima manutenção, com alertas de vencimento.
- **Almoxarifado:** técnicos enviam pedidos; almoxarifado e supervisão atualizam o andamento.
- **Manuais técnicos:** catálogo de documentos e referências fornecidos pela empresa, com links para os arquivos ou páginas oficiais.
- **Acessos:** administrador controla todo o sistema e as contas; supervisor administra clientes, equipamentos, ordens e materiais e atribui ordens a técnicos; técnico vê apenas suas ordens atribuídas, os clientes/equipamentos ligados a elas, as manutenções correspondentes, manuais e seus próprios pedidos; almoxarifado vê os pedidos e atualiza sua situação. Senhas são armazenadas com derivação PBKDF2 e a sessão usa cookie HttpOnly.

Para salvar uma ordem em PDF, abra a ordem e escolha **Salvar ordem em PDF**. Na janela do navegador, selecione **Salvar como PDF**. A assinatura fica armazenada como imagem no banco SQLite junto com a ordem.

## Dados e preparo para uso real

O banco persistente fica em `data/gestao.db` e é criado automaticamente. `CFTV_DB` permite escolher outro caminho. Para backup, feche o servidor e copie a pasta `data`; não envie o banco ao GitHub. Os dados que já estavam no banco permanecem armazenados.

Esta entrega é uma base inicial: não inclui envio de PDF por e-mail/WhatsApp, redefinição de senha, armazenamento de arquivo de manual dentro do app, assinatura digital certificada, sincronização em nuvem ou implantação pública. O modo local ainda não dá acesso pelo celular usando outra rede. Antes de usar com dados de clientes em produção, faça implantação com HTTPS, controle de backup e política de acesso adequada.

## Verificações

```bash
python3 -m unittest discover -s tests -v
node --check static/app.js
```
