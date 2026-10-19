# Segura Gestão — Segurança eletrônica

Aplicativo local em português para equipamentos, clientes, orçamentos, manutenções e calculadoras.

## Executar

Instale Python 3.10 ou superior e execute nesta pasta:

```bash
python server.py
```

No Windows, use `py server.py` ou abra `iniciar.bat`. Acesse http://127.0.0.1:8000 e mantenha o terminal aberto. Não requer pacotes adicionais.

## Primeiro uso

1. Preencha Minha empresa e envie uma logo PNG/JPEG (até 1 MB).
2. Cadastre clientes e equipamentos/serviços, com marca, unidade e preço.
3. Monte um orçamento selecionando itens e quantidades. Preencha validade, pagamento, garantia, frete, desconto e observações.
4. Salve e abra a proposta. Clique em **Salvar em PDF / imprimir** e escolha **Salvar como PDF** na janela do navegador. Desative cabeçalhos/rodapés do navegador.
5. Agende manutenções e acompanhe dias restantes e atrasos. Após concluir, clique em Reagendar para gerar a próxima visita.

As propostas guardam uma cópia dos preços e dados de cliente/empresa na emissão. Alterar cadastros não altera propostas antigas.

## Dados e limites

SQLite persistente em `data/gestao.db`, criado automaticamente. Para backup, feche o servidor e copie a pasta `data`. Para restaurar, recoloque a pasta no projeto com o servidor fechado. Não envie o banco ao GitHub.

Esta versão funciona apenas no computador local (127.0.0.1), sem login, múltiplas empresas, sincronização, notificações automáticas ou aplicativo móvel nativo. Não exponha o servidor à internet sem autenticação e infraestrutura adequada. Os prazos são atualizados ao abrir a aba.

## Calculadoras

- Armazenamento CFTV: TB decimal = câmeras × Mbps × dias × horas/dia × 3600 ÷ 8 ÷ 1.000.000; mostra também reserva de 20%.
- Fonte: A = quantidade × corrente unitária × (1 + reserva/100); W = V × A.
- Motor monofásico CA: I = potência mecânica / (V × rendimento × fator de potência).
- Motor trifásico CA: mesma fórmula com √3 no denominador e tensão entre fases.
- Motor CC: I = potência mecânica / (V × rendimento).
- Conversão: 1 cv = 735,49875 W; 1 hp mecânico = 745,699872 W.

São estimativas. Confira bitrate real e dados de placa. Não dimensiona partida, capacitor, cabos, proteção ou motor por peso do portão.

## Testar

```bash
python -m unittest discover -s tests -v
node --check static/app.js
```

Node é opcional, usado somente na verificação de sintaxe JavaScript.
