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

- Cadastro de multiplos equipamentos por nome e IP.
- Ping automatico a cada 1 segundo.
- Tabela com status online/offline, latencia e horario da ultima leitura.
- Notificacao via WhatsApp quando um equipamento fica offline por 1, 15, 30 e 60 minutos.
- Notificacao via WhatsApp quando a conexao e reestabelecida apos uma queda alertada.
- Aba de configuracoes para informar endpoint, destinatario e chave da Evolution API.
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
- `notification_config.py`: contem apenas configuracoes nao sensiveis.
- `notification_client.py`: cliente HTTP que envia mensagens para a Evolution API.
- `outage_notifier.py`: controla os limiares de queda e dispara os alertas.
- `outage_logger.py`: registra quedas e recuperacoes em arquivo texto.

## Equipamentos salvos

Ao abrir o programa, o arquivo `equipamentos.txt` e criado automaticamente caso
nao exista. Quando o programa estiver empacotado como executavel, esse arquivo
fica na mesma pasta do `.exe`. Cada equipamento fica salvo em uma linha:

```text
Nome do equipamento;192.168.0.10
```

Quando um equipamento e removido pela interface, ele tambem e removido desse
arquivo.

## Log de quedas

O arquivo `quedas_log.txt` e criado automaticamente quando a primeira queda for
detectada. Ele registra:

- inicio da queda;
- fim da queda;
- equipamento;
- IP;
- horario da queda;
- duracao total quando a conexao volta.

O log registra tambem quedas menores que 1 minuto. As notificacoes pelo WhatsApp
continuam sendo enviadas apenas a partir de 1 minuto de queda.

Quando o programa estiver empacotado como executavel, `quedas_log.txt` fica na
mesma pasta do `.exe`.

## Configuracao da notificacao

As configuracoes da Evolution API sao feitas pela propria interface do programa,
na aba `Configuracoes`.

Preencha:

- URL do endpoint da Evolution API.
- Numero ou grupo que recebera as mensagens.
- Chave da API.

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
