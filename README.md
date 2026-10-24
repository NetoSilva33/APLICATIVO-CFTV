# Segura Gestão — Segurança eletrônica

Aplicativo web responsivo e instalável como PWA, pensado para operação de empresas de segurança eletrônica em computador e celular. O servidor atual usa SQLite local e a biblioteca padrão do Python; não precisa de pacotes externos.

## Executar

Instale Python 3.10 ou superior e, nesta pasta, execute:

```bash
python3 server.py
```

Abra http://127.0.0.1:8000. No Windows, execute `py -3 server.py` ou abra `iniciar.bat`. Na primeira abertura, crie o acesso de administrador com uma senha de pelo menos 12 caracteres. O administrador cadastra os outros acessos na área **Acessos**.

### Manter a cópia local atualizada

Clone o repositório uma vez, em vez de baixar um ZIP:

```powershell
git clone https://github.com/NetoSilva33/APLICATIVO-CFTV.git
cd APLICATIVO-CFTV
```

No Windows, abra `atualizar-e-iniciar.bat` para buscar a versão mais recente da branch `main` e iniciar o app. O Git for Windows precisa estar instalado. Se preferir atualizar manualmente, pare o servidor com **Ctrl+C**, execute `git pull --ff-only origin main` nesta pasta e inicie o app de novo. As alterações chegam ao computador na próxima atualização; o app não troca de versão enquanto está aberto.

Se você já cadastrou dados usando uma pasta baixada em ZIP, pare o servidor e copie `data/gestao.db` da pasta antiga para a pasta clonada. Esse banco fica somente no computador e não é enviado ao GitHub.

O servidor escuta somente em `127.0.0.1` por padrão. Assim, esta execução serve ao próprio computador. Para acessar de outros aparelhos, hospede a aplicação atrás de HTTPS e configure o proxy antes de disponibilizar o endereço à equipe. Crie o administrador antes de expor uma instalação nova. Não exponha o servidor diretamente à internet.

## Áreas incluídas

- **Ordens de serviço:** abertura por cliente e equipamento, registro do serviço e materiais utilizados, situação do atendimento, assinatura desenhada com mouse ou tela sensível ao toque e impressão do documento em PDF pelo navegador.
- **Clientes e equipamentos:** cadastro de clientes e equipamentos vinculados, fabricante, modelo, número de série ou local.
- **Manutenção:** registro de visita, histórico por equipamento e datas da última e próxima manutenção, com alertas de vencimento.
- **Almoxarifado:** catálogo de materiais com saldo físico, quantidade reservada, saldo disponível, alerta de estoque baixo e localização. Todos os perfis podem montar pedidos com vários itens do catálogo; almoxarifado e supervisão acompanham o fluxo, e o saldo físico é baixado quando a entrega é registrada.
- **Compras:** qualquer usuário pode solicitar material ou ferramenta com quantidade, prioridade, link, justificativa, valor estimado e até três fotos. Administradores e supervisores aprovam ou recusam; o almoxarifado marca pedidos aprovados como realizados e recebidos.
- **Manuais técnicos:** catálogo de documentos e referências fornecidos pela empresa, com links para os arquivos ou páginas oficiais.
- **Acessos:** administrador controla todo o sistema e as contas, pode redefinir senhas e encerra as sessões antigas da pessoa; supervisor administra clientes, equipamentos, ordens e acompanha solicitações; técnico vê apenas suas ordens atribuídas, os clientes/equipamentos ligados a elas, as manutenções correspondentes, manuais e seus próprios pedidos; almoxarifado administra o catálogo e atualiza os pedidos de estoque e compras. Todos os perfis podem consultar o catálogo e abrir solicitações de material ou compra. Senhas são armazenadas com derivação PBKDF2 e a sessão usa cookie HttpOnly.

Para salvar uma ordem em PDF, abra a ordem e escolha **Salvar ordem em PDF**. Na janela do navegador, selecione **Salvar como PDF**. A assinatura fica armazenada como imagem no banco SQLite junto com a ordem.

## Dados e preparo para uso real

O banco persistente fica em `data/gestao.db` e é criado automaticamente. `CFTV_DB` permite escolher outro caminho. Para backup, feche o servidor e copie a pasta `data`; não envie o banco ao GitHub. Os dados que já estavam no banco permanecem armazenados.

Esta entrega é uma base inicial: não inclui envio de PDF por e-mail/WhatsApp, armazenamento de arquivo de manual dentro do app, assinatura digital certificada, sincronização em nuvem ou implantação pública. O modo local ainda não dá acesso pelo celular usando outra rede. Antes de usar com dados de clientes em produção, faça implantação com HTTPS, controle de backup e política de acesso adequada.

## Verificações

```bash
python3 -m unittest discover -s tests -v
node --check static/app.js
```
