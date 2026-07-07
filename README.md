# Monitor de Equipamentos na Rede

Aplicacao em Python com Tkinter para monitorar equipamentos de rede via ping.

## Como executar

```powershell
python main.py
```

Se o comando `python` nao estiver configurado no Windows, tente:

```powershell
py main.py
```

## Como gerar executavel

Com o PyInstaller instalado, gere o executavel com:

```powershell
py -m PyInstaller --onefile --windowed --icon="icon.ico" --name PingaNiMim main.py
```

O executavel sera gerado em:

```text
dist\PingaNiMim.exe
```

Os arquivos locais `equipamentos.txt` e `configuracoes_sensiveis.dat` serao
criados na mesma pasta do executavel. Assim, voce pode deixar o `.exe` em uma
pasta fixa e criar apenas um atalho para ele na area de trabalho.

## Funcionalidades atuais

- Cadastro de multiplos equipamentos por nome, IP e grupo.
- Edicao de equipamentos ja cadastrados pela propria tela.
- Intervalo de ping configuravel por equipamento.
- Dashboard com cards de total, online, offline, instavel, oscilando, aguardando e manutencao.
- Resumo por grupo e filtros por grupo, status e busca por nome/IP/grupo.
- Tabela com status, latencia, horario da ultima leitura, tempo offline e ultimo evento.
- Historico rapido de eventos recentes na tela principal.
- Confirmacao de offline apenas apos falhas consecutivas configuraveis.
- Deteccao de equipamentos oscilando.
- Janela de manutencao por equipamento para silenciar alertas temporariamente.
- Notificacao via WhatsApp em intervalos de queda definidos pelo usuario.
- Intervalos de notificacao especificos por grupo, com intervalo global como padrao.
- Notificacao via WhatsApp quando a conexao e reestabelecida apos uma queda alertada.
- Aba de configuracoes para informar endpoint, destinatario e chave da Evolution API.
- Fila de envio de notificacoes para processar alertas simultaneos sem disputar a API.
- Armazenamento criptografado das configuracoes sensiveis.
- Log local de inicio e fim de quedas em `quedas_log.txt`.
- Salvamento automatico dos equipamentos em `equipamentos.txt`.
- Remocao de equipamentos em monitoramento.

## Estrutura

- `main.py`: inicia a aplicacao.
- `equipment_store.py`: salva e carrega os equipamentos em arquivo texto.
- `monitor_app.py`: contem a interface grafica Tkinter.
- `ping_monitor.py`: contem a logica de ping e as threads de monitoramento.
- `secure_settings.py`: salva e carrega configuracoes sensiveis com criptografia local.
- `notification_config.py`: contem padroes e validacao dos intervalos de notificacao.
- `notification_client.py`: cliente HTTP que envia mensagens para a Evolution API.
- `outage_notifier.py`: controla os limiares de queda e dispara os alertas.
- `outage_logger.py`: registra quedas e recuperacoes em arquivo texto.

## Equipamentos salvos

Ao abrir o programa, o arquivo `equipamentos.txt` e criado automaticamente caso
nao exista. Quando o programa estiver empacotado como executavel, esse arquivo
fica na mesma pasta do `.exe`. Cada equipamento fica salvo em uma linha:

```text
Nome do equipamento;192.168.0.10;Grupo;IntervaloPingSegundos
```

Arquivos antigos nos formatos `Nome;IP` e `Nome;IP;Grupo` continuam sendo
lidos. Nesses casos, o equipamento entra no grupo `Sem grupo` quando nao houver
grupo salvo e usa intervalo de ping de 1 segundo quando nao houver intervalo.

Quando um equipamento e removido pela interface, ele tambem e removido desse
arquivo.

Para editar um equipamento, selecione a linha na tabela, clique em `Editar
selecionado`, altere nome, IP, grupo ou intervalo de ping e clique em `Salvar
edicao`. O monitoramento desse equipamento e reiniciado com os novos dados.

## Log de quedas

O arquivo `quedas_log.txt` e criado automaticamente quando a primeira queda for
detectada. Ele registra:

- inicio da queda;
- fim da queda;
- equipamento;
- IP;
- horario da queda;
- duracao total quando a conexao volta.

O log registra tambem quedas menores que o primeiro intervalo configurado. As
notificacoes pelo WhatsApp sao enviadas quando a queda alcanca os intervalos
definidos na aba `Configuracoes`.

Quando o programa estiver empacotado como executavel, `quedas_log.txt` fica na
mesma pasta do `.exe`.

## Configuracao da notificacao

As configuracoes da Evolution API sao feitas pela propria interface do programa,
na aba `Configuracoes`.

Preencha:

- URL do endpoint da Evolution API.
- Numero ou grupo que recebera as mensagens.
- Chave da API.
- Intervalos que devem gerar alerta quando a queda continuar.
- Intervalos especificos por grupo, quando algum grupo precisar de outro ritmo.
- Quantidade de falhas seguidas para confirmar offline.
- Quantidade de mudancas online/offline e janela em minutos para marcar oscilacao.

O campo de intervalos aceita valores separados por virgula, ponto e virgula ou
espaco. Use `s` para segundos, `m` para minutos e `h` para horas. Valores sem
unidade continuam sendo tratados como minutos. Exemplos:

```text
5s, 30s, 1m, 5m
```

```text
1, 5, 15
```

O motor de monitoramento usa por padrao 3 falhas seguidas para confirmar
offline e marca oscilacao quando ha 4 mudancas de estado dentro de 10 minutos.

Na secao `Alertas por grupo`, escolha um grupo, informe os intervalos e clique
em `Salvar grupo`. Para voltar a usar o intervalo global, selecione o grupo e
clique em `Usar global`.

Ao clicar em `Salvar configuracoes`, o programa cria o arquivo local:

```text
configuracoes_sensiveis.dat
```

Esse arquivo fica no `.gitignore` e nao deve ser enviado ao GitHub.
Quando o programa estiver empacotado como executavel, esse arquivo tambem fica
na mesma pasta do `.exe`.

As informacoes sao criptografadas usando a DPAPI do Windows. Isso significa que
o arquivo criptografado so pode ser lido pelo mesmo usuario do Windows que
salvou as configuracoes.

Em uma maquina nova, ou com outro usuario do Windows, basta abrir a aba
`Configuracoes` e preencher os dados novamente.

### Onde encontrar os dados da Evolution API

- URL do endpoint: fica na documentacao da Evolution API, em envio de mensagem
  de texto. O formato costuma ser `https://SEU_SERVIDOR/message/sendText/NOME_DA_INSTANCIA`.
- Chave da API: e a `apikey` ou chave da instancia no painel da Evolution API.
- Numero ou grupo: para telefone, use DDI + DDD + numero. Para grupo, use o JID
  terminado em `@g.us`.

Uma forma mais direta de achar o JID do grupo e consultar a propria Evolution
API:

```text
GET https://SEU_SERVIDOR/group/fetchAllGroups/NOME_DA_INSTANCIA?getParticipants=false
Header: apikey: SUA_CHAVE
```

A resposta lista os grupos da instancia. Copie o campo `id` do grupo desejado,
por exemplo:

```text
120363295648424210@g.us
```
