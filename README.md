# Segura Gestão — Segurança eletrônica

Aplicativo web responsivo e instalável como PWA, pensado para operação de empresas de segurança eletrônica em computador e celular. A versão atual oferece uma base multempresa de testes usando SQLite local e a biblioteca padrão do Python; não precisa de pacotes externos.

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
- **Almoxarifado:** catálogo editável de materiais com saldo físico, reservado e disponível, alerta de estoque baixo, localização e unidade por item (como caixa, unidade ou metro). Todos os perfis podem montar pedidos com vários itens do catálogo; almoxarifado e supervisão acompanham o fluxo, e o saldo físico é baixado quando a entrega é registrada. Dicas nos campos explicam as categorias e unidades.
- **Compras:** qualquer usuário pode solicitar material ou ferramenta com quantidade, prioridade, link, justificativa, valor estimado e até três fotos. Administradores e supervisores aprovam ou recusam; o almoxarifado marca pedidos aprovados como realizados e recebidos.
- **Manuais técnicos:** catálogo de documentos e referências fornecidos pela empresa, com links para os arquivos ou páginas oficiais.
- **Acessos:** administrador controla todo o sistema e as contas, pode editar nome, usuário, perfil, situação e senha, além de excluir contas; o sistema impede excluir a própria conta ou remover o último administrador ativo, encerra sessões da conta editada e mantém ordens e solicitações históricas após uma exclusão. Supervisor administra clientes, equipamentos, ordens e acompanha solicitações; técnico vê apenas suas ordens atribuídas, os clientes/equipamentos ligados a elas, as manutenções correspondentes, manuais e seus próprios pedidos; almoxarifado administra o catálogo e atualiza os pedidos de estoque e compras. Todos os perfis podem consultar o catálogo e abrir solicitações de material ou compra. Senhas são armazenadas com derivação PBKDF2 e a sessão usa cookie HttpOnly.

## Empresas e planos de teste

Na primeira execução, informe o nome da sua empresa e crie o primeiro administrador. Esse primeiro administrador também recebe o acesso de vendedor ao portal separado em `/plataforma` (por exemplo, `http://127.0.0.1:8000/plataforma`). O portal serve para cadastrar empresas clientes, editar seus dados, conferir a quantidade de acessos e controlar plano e situação. Ele não mostra ordens, clientes, equipamentos, estoque nem solicitações das empresas. Cada empresa usa a página inicial do produto e tem seus próprios dados, filtrados também no servidor.

O **código da empresa** aparece na área Empresa. Funcionários podem informá-lo na tela de login; se o nome de usuário for exclusivo, o código pode ficar vazio. O vendedor pode acompanhar as empresas, editar nome e documento, alterar o plano e suspender ou reativar o acesso pelo portal comercial. Os planos Essencial, Profissional e Empresa limitam os acessos a 10, 50 e 500, respectivamente. Empresas cadastradas pelo portal começam com uma avaliação de 14 dias. A gestão de planos é manual nesta versão: cobrança e renovação automáticas ainda não estão conectadas.

Administradores e supervisores têm abas de alertas, relatórios e auditoria. O administrador também pode exportar os dados da própria empresa em JSON pela área Empresa. Instalações antigas com um banco de dados de uma única empresa são migradas automaticamente no primeiro início desta versão. Faça uma cópia do arquivo `data/gestao.db` antes de atualizar.

Para salvar uma ordem em PDF, abra a ordem e escolha **Salvar ordem em PDF**. Na janela do navegador, selecione **Salvar como PDF**. A assinatura fica armazenada como imagem no banco SQLite junto com a ordem.

## Dados e preparo para uso real

O banco persistente fica em `data/gestao.db` e é criado automaticamente. `CFTV_DB` permite escolher outro caminho. Para backup, feche o servidor e copie a pasta `data`; não envie o banco ao GitHub. Os dados que já estavam no banco permanecem armazenados.

Esta entrega é uma base para testes multempresa no próprio computador. O menu lateral rola quando as opções não cabem na tela. A versão ainda usa SQLite local e não inclui hospedagem pública, PostgreSQL, cobrança automática, convites por e-mail, envio de PDF por e-mail/WhatsApp, armazenamento de arquivo de manual dentro do app, assinatura digital certificada ou sincronização em nuvem. Para testar em vários aparelhos ou usar dados reais em produção, ainda é necessário configurar hospedagem com HTTPS, banco de dados gerenciado, backups e política de acesso adequada. Não exponha o servidor de desenvolvimento diretamente à internet.

## Verificações

```bash
python3 -m unittest discover -s tests -v
node --check static/app.js
```
