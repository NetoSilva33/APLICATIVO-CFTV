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

O servidor escuta somente em `127.0.0.1` por padrão. Assim, esta execução serve ao próprio computador. Para acessar de outros aparelhos, hospede a aplicação atrás de HTTPS e configure o proxy antes de disponibilizar o endereço à equipe. Crie o administrador da empresa e o login comercial antes de expor uma instalação nova. Não exponha o servidor diretamente à internet.

## Áreas incluídas

- **Ordens de serviço:** abertura por cliente e equipamento, registro do serviço e materiais utilizados, situação do atendimento, assinatura desenhada com mouse ou tela sensível ao toque e impressão do documento em PDF pelo navegador.
- **Agenda técnica:** visão dos atendimentos programados nos próximos sete dias, com data, horário, duração e técnico. A OS pode ser reprogramada pela administração ou supervisão.
- **Preventivas recorrentes:** configure intervalo de 1 a 36 meses e um técnico para cada equipamento. Ao abrir ou atualizar o aplicativo depois do vencimento, ele cria uma OS preventiva uma única vez; ao concluir essa OS, calcula o próximo prazo.
- **Checklist e fotos na OS:** listas iniciais para CFTV, acesso/automação ou atendimento geral, itens marcados durante o serviço e até três fotos de até 750 KB cada. Checklist, fotos e assinatura aparecem na impressão em PDF.
- **Portal do cliente:** em **Clientes**, use **Link portal** para emitir um link revogável. O cliente vê somente seus equipamentos, ordens, fotos e manutenções e pode imprimir cada ordem em PDF. Gerar outro link invalida o anterior. O link precisa ser compartilhado pela equipe.
- **Consulta por QR:** a página do equipamento mostra etiqueta imprimível e link direto para abri-lo no aplicativo. A imagem do QR é gerada pelo serviço externo QRServer e inclui apenas a URL do sistema e o identificador aleatório do equipamento; precisa de conexão para carregar.
- **Modo offline do técnico:** o navegador guarda uma cópia local dos dados já carregados e permite salvar atualizações de ordens e manutenções atribuídas; a fila é enviada quando a conexão volta. Cadastros administrativos, estoque e aprovações exigem conexão. A cópia local fica neste navegador e é apagada ao sair da conta.
- **Indicadores:** relatórios mostram ordens abertas e concluídas, ordens criadas nos últimos 30 dias, preventivas atrasadas, materiais com estoque baixo, compras aguardando aprovação e volume/conclusão por técnico.
- **Atalhos de contato:** ordens com telefone ou e-mail do cliente oferecem links que preenchem uma mensagem no WhatsApp ou no programa de e-mail. A pessoa revisa e envia a mensagem; o sistema não dispara comunicações automaticamente.
- **Clientes e equipamentos:** cadastro de clientes e equipamentos vinculados, fabricante, modelo, número de série ou local.
- **Manutenção:** registro de visita, histórico por equipamento e datas da última e próxima manutenção, com alertas de vencimento.
- **Almoxarifado:** catálogo editável de materiais com saldo físico, reservado e disponível, alerta de estoque baixo, localização e unidade por item (como caixa, unidade ou metro). Todos os perfis podem montar pedidos com vários itens do catálogo; almoxarifado e supervisão acompanham o fluxo, e o saldo físico é baixado quando a entrega é registrada. Dicas nos campos explicam as categorias e unidades.
- **Compras:** qualquer usuário pode solicitar material ou ferramenta com quantidade, prioridade, link, justificativa, valor estimado e até três fotos. Administradores e supervisores aprovam ou recusam; o almoxarifado marca pedidos aprovados como realizados e recebidos.
- **Manuais técnicos:** catálogo de documentos e referências fornecidos pela empresa, com links para os arquivos ou páginas oficiais.
- **Dados comerciais da empresa:** nome, CNPJ, código de acesso, plano e situação são controlados pelo vendedor no portal comercial. As contas das empresas não têm as abas Empresa e Auditoria nem exportação administrativa dos dados. O vendedor fornece o código de acesso aos funcionários que precisarem dele para entrar.
- **Acessos:** administrador controla todo o sistema e as contas, pode editar nome, usuário, perfil, situação e senha, além de excluir contas; o sistema impede excluir a própria conta ou remover o último administrador ativo, encerra sessões da conta editada e mantém ordens e solicitações históricas após uma exclusão. Supervisor administra clientes, equipamentos, ordens e acompanha solicitações; técnico vê apenas suas ordens atribuídas, os clientes/equipamentos ligados a elas, as manutenções correspondentes, manuais e seus próprios pedidos; almoxarifado administra o catálogo e atualiza os pedidos de estoque e compras. Todos os perfis podem consultar o catálogo e abrir solicitações de material ou compra. Senhas são armazenadas com derivação PBKDF2 e a sessão usa cookie HttpOnly.

## Empresas e planos de teste

Na página inicial, crie a empresa de teste e seu administrador. O login comercial é independente: com o servidor parado, crie-o pelo terminal com `py -3 server.py --create-platform-admin` no Windows ou `python3 server.py --create-platform-admin` nos outros sistemas. O comando pede nome, usuário e senha e só pode ser usado uma vez. Depois, inicie o servidor e acesse `/plataforma` (por exemplo, `http://127.0.0.1:8000/plataforma`). Faça essa configuração antes de entregar o endereço a clientes; o portal não oferece cadastro comercial pela internet. Ele serve para cadastrar empresas clientes, editar seus dados, conferir a quantidade de acessos e controlar plano e situação. Não mostra ordens, clientes, equipamentos, estoque nem solicitações das empresas. Cada empresa usa a página inicial do produto e tem seus próprios dados, filtrados também no servidor.

O vendedor pode acompanhar as empresas, editar nome, CNPJ/documento, código de acesso, plano e situação pelo portal comercial. Funcionários podem informar o código recebido na tela de login; se o nome de usuário for exclusivo, o código pode ficar vazio. Os planos Essencial, Profissional e Empresa limitam os acessos a 10, 50 e 500, respectivamente. Empresas cadastradas pelo portal começam com uma avaliação de 14 dias, com contagem de dias para o vencimento. O vendedor pode iniciar ou renovar a vigência em períodos mensal, trimestral, semestral ou anual; renovações antecipadas são somadas ao prazo atual. O acesso da empresa é bloqueado quando a avaliação ou assinatura vence. Cobrança e renovação continuam manuais, sem integração de pagamento automático.

Administradores e supervisores têm abas de alertas e relatórios. O histórico de auditoria e os dados comerciais da empresa não são expostos às contas de clientes; alterações de identificação e plano são feitas pelo vendedor. Instalações antigas com um banco de dados de uma única empresa são migradas automaticamente no primeiro início desta versão. Faça uma cópia do arquivo `data/gestao.db` antes de atualizar.

Para salvar uma ordem em PDF, abra a ordem e escolha **Salvar ordem em PDF**. Na janela do navegador, selecione **Salvar como PDF**. A assinatura fica armazenada como imagem no banco SQLite junto com a ordem.

## Dados e preparo para uso real

O banco persistente fica em `data/gestao.db` e é criado automaticamente. `CFTV_DB` permite escolher outro caminho. Para backup, feche o servidor e copie a pasta `data`; não envie o banco ao GitHub. Os dados que já estavam no banco permanecem armazenados.

Esta entrega é uma base para testes multempresa no próprio computador. O menu lateral rola quando as opções não cabem na tela. A versão ainda usa SQLite local e não inclui hospedagem pública, PostgreSQL, cobrança automática, convites por e-mail, envio de PDF por e-mail/WhatsApp, armazenamento de arquivo de manual dentro do app, assinatura digital certificada ou sincronização em nuvem. Para testar em vários aparelhos ou usar dados reais em produção, ainda é necessário configurar hospedagem com HTTPS, banco de dados gerenciado, backups e política de acesso adequada. Não exponha o servidor de desenvolvimento diretamente à internet.

O modo offline mantém dados da empresa no armazenamento do navegador até a pessoa sair da conta; use-o somente em aparelhos controlados pela empresa. A recorrência é verificada quando o aplicativo consulta os dados, não por uma tarefa em segundo plano com o servidor fechado. A imagem de QR depende do QRServer; o endereço e o identificador aleatório do equipamento são enviados ao serviço para compor o QR, sem nome ou cadastro do cliente. Mensagens WhatsApp/e-mail são atalhos manuais. O portal de cliente é de consulta e impressão; o cliente ainda não assina nem altera ordens pelo portal.

## Verificações

```bash
python3 -m unittest discover -s tests -v
node --check static/app.js
node --check static/client.js
node --check static/sw.js
python3 -m py_compile server.py tests/test_server.py
```
