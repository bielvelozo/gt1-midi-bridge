# Handoff — Ponte MIDI para Boss GT-1 → plugins (Neural DSP Archetype)

> Documento de passagem de bastão. Contém todo o contexto necessário para
> continuar o projeto do zero, sem depender da conversa anterior. Feito para
> ser entregue a um agente (Claude Code).

---

## 1. Objetivo

Usar a **Boss GT-1** como **controlador MIDI** para o plugin **Neural DSP
Archetype: Mateus Asato** (e, por tabela, qualquer outro plugin/DAW).

Ações desejadas, em ordem de prioridade:

1. **Trocar de preset** do plugin usando os footswitches de patch (▲/▼) da GT-1.
2. **Ligar/desligar** um pedal do plugin (ex.: Drive 1) usando o footswitch **CTL1**.
3. **Controle contínuo** (wah/volume/gain) usando o **pedal de expressão (EXP1)**.

---

## 2. O problema central (por que não é trivial)

A GT-1 **não envia CC nem PC utilizável pelos footswitches**. Isso foi
verificado a fundo:

- A GT-1 só tem **MIDI via USB** (não tem DIN de 5 pinos). É USB MIDI
  class-compliant, mas o **driver USB oficial da Boss** é recomendado no Windows.
- **Não existe menu "MIDI SETTING" na GT-1** (ao contrário da GT-001, GT-100,
  GT-1000 e MS-3, que têm saída MIDI configurável). Não dá para atribuir MIDI
  aos footswitches pela interface do aparelho.
- Ao **pisar num pedal, a GT-1 NÃO transmite nada espontaneamente**. Confirmado
  com o monitor Pocket MIDI: nada aparece nem na janela de mensagens comuns nem
  na de System Exclusive quando se pisa/troca patch apenas ouvindo.

**Porém** — e aqui está a virada — a GT-1 **RESPONDE quando é perguntada**, via
SysEx (polling). Isso foi provado porque o editor open-source **FxFloorBoard
conecta, lê os patches e o "Auto Sync" funciona**. Ou seja, software de
terceiros consegue interrogar a GT-1 e ela responde com o estado atual.

**Conclusão / estratégia:** construir uma **ponte** em Python que faz _polling_
do estado da GT-1 via SysEx (RQ1→DT1), detecta mudanças e emite Program
Change / Control Change numa **porta MIDI virtual** que o plugin escuta. É
exatamente o que o Tone Studio/FxFloorBoard fazem por baixo dos panos, só que
convertendo o resultado em MIDI padrão.

---

## 3. Protocolo SysEx da GT-1 (CONFIRMADO)

Fonte: **Service Notes oficiais da GT-1** (exemplos reais de backup de dados).

Formato do pedido de leitura (RQ1):

```
F0 41 7F 00 00 00 30 11 <addr:4 bytes> <size:4 bytes> <checksum> F7
```

| Campo        | Valor         | Observação                                |
| ------------ | ------------- | ----------------------------------------- |
| Manufacturer | `41`          | Roland                                    |
| Device ID    | `7F`          | Broadcast (não precisa saber o ID exato)  |
| Model ID     | `00 00 00 30` | **GT-1** (4 bytes)                        |
| Command RQ1  | `11`          | Pedido de leitura                         |
| Command DT1  | `12`          | Resposta com dados (e também escrita)     |
| Checksum     | Roland        | `(0x80 - (sum(addr+size) & 0x7F)) & 0x7F` |

Exemplos reais das Service Notes (leitura para backup):

```
F0 41 7F 00 00 00 30 11 00 00 00 00 00 02 00 00 7E F7   # área de sistema
F0 41 7F 00 00 00 30 11 10 00 00 00 00 63 00 00 0D F7   # dados de patch
```

Notas dos exemplos:

- Os **dados de patch de usuário** ficam na base de endereço `0x10 00 00 00`.
- `0x63` = 99 → confere com os **99 patches de usuário** (U01–U99).
- A resposta (DT1) vem como: `F0 41 7F 00 00 00 30 12 <addr:4> <valor...> <chk> F7`.

---

## 4. Estado atual do código

Já existe um script **`ponte_gt1.py` (v0 / probe)** — deve acompanhar este
handoff na mesma pasta. Ele:

- Abre a porta da GT-1 (procura por "GT-1" no nome).
- Manda um RQ1 para um endereço (default `00 00 00 00`, configurável por CLI).
- Imprime qualquer DT1 recebido, mostrando `addr` e `valor`.
- Faz polling a 25 Hz.

**Trechos-chave já implementados e testados na lógica (reaproveitar):**

```python
MODEL_ID = [0x00, 0x00, 0x00, 0x30]  # GT-1
DEV_ID, RQ1, DT1 = 0x7F, 0x11, 0x12

def roland_checksum(payload):
    return (0x80 - (sum(payload) & 0x7F)) & 0x7F

def rq1_sysex_data(addr, size):        # bytes ENTRE F0 e F7 (formato do mido)
    body = addr + size
    return [0x41, DEV_ID] + MODEL_ID + [RQ1] + body + [roland_checksum(body)]
```

Uso do probe:

```
pip install mido python-rtmidi
python ponte_gt1.py                       # lê 00 00 00 00
python ponte_gt1.py 00 00 00 00 00 00 00 10   # lê endereço/tamanho custom
```

---

## 5. Roadmap (marcos)

- [x] **Marco 1 — Provar comunicação Python↔GT-1.** ✅ 2026-07-02
      Confirmado: a GT-1 responde DT1 ao RQ1. Portas no Windows:
      **OUT = "GT-1 1"**, **IN = "GT-1 0"** (qualquer par funciona, mas
      padronizamos esse). Gotcha: o backend WinMM do rtmidi falha
      esporadicamente ao enviar SysEx → usar envio com retry + reopen.

- [x] **Marco 2 — Descobrir os endereços de interesse.** ✅ 2026-07-02
      Descobertos empiricamente (varredura diferencial: baseline + diff
      enquanto se opera a pedaleira). **Mapa confirmado:**

      | O quê | Endereço | Formato |
      | --- | --- | --- |
      | **Nº do patch atual** | `00 01 00 00` | 2 bytes (MSB\*128+LSB, 0-based) — **só responde com o modo editor ativado**: DT1 `7F 00 00 01` = `01` (mesmo comando que o Tone Studio manda ao conectar) |
      | Nome do patch atual (buffer temp) | `60 00 00 00` | 16 bytes ASCII |
      | Patch temporário completo | `60 00 00 00`..páginas `10` | ~2,2 KB |
      | CTL1 → on/off do efeito alvo | `60 00 00 5B` (com CTL1=SYSTEM) | 1 byte, 0/1 |
      | EXP1 → posição do pedal | `60 00 06 33` | 1 byte, 0x00–0x64 (0–100) |
      | Patch de usuário U(N+1) | `10 <N> 00 00`, N=0..98 | nome nos 16 primeiros bytes |
      | Patches de preset | `20 00 00 00` em diante | (fonte: FxFloorBoard) |

      Bases que respondem a RQ1: `00, 10, 20, 40, 60, 70, 7F`.
      `70` = ruído de DSP (ignorar). O registrador de patch `00 01 00 00`
      veio do código-fonte do GT-1 FxFloorBoard (`SysxIO::requestPatchChange`
      escreve nele para trocar de patch) e foi confirmado como **legível**
      na GT-1 real. Escrever nele também troca o patch (não usado ainda).

- [x] **Marco 3 — v1 da ponte (troca de preset).** ✅ TESTADA de ponta a ponta
      `ponte.py`: ativa modo editor, faz polling de `00 01 00 00` →
      **Program Change** (U01=PC0..U99=PC98) na porta loopMIDI "GT1-Bridge".
      Confirmado com monitor na porta virtual: PC 13/14/15 ao trocar patch.

- [x] **Marco 4 — CTL1 e pedal de expressão.** ✅ TESTADA de ponta a ponta
      CTL1 (`60 00 00 5B`) → CC80. EXP1 (`60 00 06 33`) → CC11 (0..127).
      Polling a 20 Hz — o sweep do EXP1 saiu suave (~50 ms entre updates).
      **CTL1 universal:** com o CTL1 em `SYSTEM` (MENU→PREF→CTL1→SYSTEM) ele
      controla o mesmo efeito em TODOS os patches, cujo on/off fica em
      `60 00 00 5B` → a ponte detecta em qualquer patch. (O endereço antigo
      `60 00 11 19` era o alvo do CTL1 em modo PATCH num patch específico;
      trocar a atribuição do CTL1 muda o endereço a monitorar.)
      Marco 6 (CTL1 dinâmico por patch) foi descartado — não precisa: o
      plugin Neural tem MIDI map GLOBAL (não por preset), e o CTL1=SYSTEM
      resolve o caso de uso. Tap tempo foi considerado inviável (polling a
      50 ms é impreciso pra tap).

- [x] **Marco 5 — Robustez.** ✅ COMPLETO e validado (exe rtmidi testado
      de ponta a ponta pós-reboot: conecta, patch/CTL1/EXP1 todos OK).
      Reescrita v2 de `ponte.py`:
      - **config.json** gerado na 1a execução (canal, CCs, modo CTL1, poll, portas).
      - **Auto-reconexão** in-process (espera a GT-1 voltar) — TESTADO ✅.
      - **Supervisor**: processo pai relança a ponte se ela morrer, inclusive
        no **crash nativo do rtmidi ao remover o USB** (exit 0xC0000005) —
        TESTADO ✅ (recupera em ~7s).
      - **Log** em `ponte.log` + console.
      - **.exe** via PyInstaller (`dist\ponte.exe`), `LEIA-ME.md` escrito.
      - Migrado de **mido → python-rtmidi direto** (polling puro, sem callback):
        remove dependência e resolve travas do mido no frozen (backend
        auto-detect + open_input com callback). Fix chave no .exe:
        `os.environ["MIDO_BACKEND"]` já não se aplica (sem mido); usa
        `--collect-all rtmidi` no build.

      **⚠️ Lição / gotcha de teste (IMPORTANTE):** matar o processo à força
      (`Stop-Process -Force` / kill) **com a porta de ENTRADA aberta** deixa a
      porta MIDI da GT-1 **travada no WinMM** — aberturas seguintes de
      `open_port` congelam (trava em C segurando o GIL). Replug curto e até
      power-cycle do aparelho NÃO limpam (o Windows acumula dezenas de
      endpoints MIDI fantasma `Unknown` e reusa a instância travada). Só
      **reboot** (ou disable/enable no Device Manager, precisa admin) limpa.
      → Sempre encerrar a ponte LIMPO (Ctrl+C / fechar janela); o worker fecha
      a porta num `finally` e o supervisor encerra o filho com carência antes
      do `os._exit`. NÃO usar kill -force com a ponte rodando.

      _✅ VALIDADO 2026-07-03: `dist\ponte.exe` (rtmidi) numa porta limpa
      conecta na hora e emite PC (troca de patch), CC80 (CTL1 universal,
      todos os patches) e CC11 (EXP1 suave). O "travamento no frozen" era
      sempre a porta travada — o exe está OK._

- [x] **Marco 6 — Knobs como controladores MIDI + leitura de nome.** ✅ 2026-07-03
      - **Knobs 1/2/3 como CC contínuo:** os knobs são encoders infinitos e não
        têm posição crua legível (testado: em OFF não mudam nada). Solução:
        atribuir o knob (MENU→KNOB SETTING) a um parâmetro de um efeito **OFF**
        (ex.: ROTARY LEVEL em `60 00 10 1D`) — girar muda um valor inativo
        (não afeta o som) que a ponte lê → CC. Config em `config.json` →
        `"knobs": [{cc, address, max}]`. Os 3 knobs validados (sweep 0-100 →
        CC 0-127):
        - knob1 = ROTARY LEVEL → `60 00 10 1D` → CC12
        - knob2 = RV:LEVEL     → `60 00 06 18` → CC13
        - knob3 = RV:DLELV     → `60 00 10 74` → CC14
        (Cada param tem um byte "espelho" ao lado que se move inverso — ignorar;
        usar o byte que varre 0→0x64 limpo.) Botões de efeito NÃO servem
        (ligá-los processa a DI que vai pro plugin).
      - **Nome do patch:** trocado o pre-load dos 99 nomes (frágil: falhava
        parado em preset, e deixava o boot lento 6-21s) por ler o nome do
        **buffer temporário** (`60 00 00 00`, 16 bytes) a cada ciclo → boot
        instantâneo, nome certo pra user E preset. Label: `U{n+1}` p/ n≤98,
        senão `#{n}`; PC só sai p/ n≤127 (presets altos são ignorados no MIDI).

---

## 6. Decisões de arquitetura / gotchas

- **Ler CONSEQUÊNCIAS, não o botão.** Não tentar ler o estado momentâneo do
  switch. Em vez disso: para o CTL1, ler o **on/off do efeito** que ele
  controla; para patch, ler o **número do patch**; para o pedal, ler o
  **parâmetro alvo**. Esses valores existem como parâmetros endereçáveis.
- **Porta MIDI é exclusiva no Windows**: só 1 app por vez. A ponte precisa da
  porta livre → fechar Tone Studio/FxFloorBoard ao rodar.
- **Driver USB oficial da Boss GT-1** deve estar instalado.
- **mido + python-rtmidi**: em `mido.Message('sysex', data=...)`, `data` são os
  bytes ENTRE `F0` e `F7` (não incluir F0/F7). O `msg.data` recebido idem.
- **Latência do polling**: footswitch ~20–50 ms (aceitável); pedal de expressão
  vai ficar **grosso e com atraso** — não esperar sweep de wah suave. Talvez
  valha um polling mais rápido só quando o pedal está em movimento.
- **Risco conhecido**: se o estado que queremos não tiver endereço legível,
  usar um proxy (ex.: o efeito on/off no lugar do CTL1 em si).

---

## 7. Lado do plugin (Neural DSP) — TESTADO ✅ 2026-07-02

- Selecionar a **porta virtual** ("GT1-Bridge") como entrada MIDI do plugin
  (standalone) ou da faixa na DAW.
- **Controles (drive, wah, volume...)**: botão direito no controle →
  **"Enable MIDI Learn"** → acionar o controlador. Funcionou com CC80 (CTL1)
  e CC11 (EXP1).
- **Gotcha do toggle (CTL1)**: cada controle do Neural reage diferente ao CC.
  A ponte tem 3 modos em `config.json` → `ctl1.mode`:
  - `"switch"` (127 liga / 0 desliga) — controles que SEGUEM o valor. É o que
    funcionou pro pedal on/off testado. **Recomendado.**
  - `"trigger"` (127 a cada pisada) — controles que alternam a cada mensagem.
  - `"pulse"` (127 depois 0 a cada pisada) — controles que alternam na borda
    de subida (footswitch momentâneo).
  Se um não funcionar (só liga, ou pisa 2x), troca o modo no config.
- **Presets por Program Change**: o MIDI Learn de preset NÃO aprende PC.
  O caminho certo: janela **MIDI Mappings** (ícone de conector MIDI no canto
  inferior esquerdo) → botão **"+"** → **"Program Change Preset"** → escolher
  o nº do PC e o preset. Uma linha por patch (U*N* da GT-1 = PC *N−1*;
  a ponte imprime o número de cada patch no log). Confirmado funcionando.
- Em DAW, garantir o roteamento do MIDI até a faixa do plugin (é comum
  funcionar em standalone e "não funcionar" na DAW por falta de roteamento;
  no Ableton, PC não é repassado ao plugin — usar mapeamento por CC lá).

---

## 7.1. GT-1 como interface de áudio (monitoração) — investigado 2026-07-02

**Problema:** usando a GT-1 como interface USB + plugin, o OUTPUT dela toca
**o som da própria GT-1 (efeitos) + o retorno do plugin** ao mesmo tempo.

**Causas (duas, independentes):**

1. **DIRECT MONITOR ligado.** Parâmetro no menu (MENU → USB → DIRECT MONITOR).
   Quando ON, a GT-1 monitora localmente o próprio som processado. Quando OFF,
   toca **só o retorno do USB** (o plugin).
   - ⚠️ **Não pode ser salvo**: volta pra ON a cada vez que liga a GT-1.
   - ⚠️ **NÃO é automatizável por SysEx.** Investigado a fundo: (a) alternar no
     menu não muda nenhum byte legível — é comando volátil; (b) escrever nos
     candidatos `00 00 00 54`/`55` não faz nada no som; (c) a GT-1 **rejeita**
     escritas no "USB I/O mode" (`00 00 00 50` só aceita `00`/Normal; recusa
     Dry Out/Re-Amp). Conclusão: os controles de USB no fonte do FxFloorBoard
     são **herança do GT-100 e não funcionam na GT-1**. DIRECT MONITOR é
     **função só de painel** → tem que fazer no braço ao ligar (3 botões).
2. **USB OUT sempre manda o som PROCESSADO** (não há tap "dry"; confirmado pela
   rejeição do Dry Out). Então o plugin processa o tom já-processado da GT-1 →
   amp em cima de amp.

**Solução adotada (escolha do usuário): toggle manual + patch DI.**

- **Patch DI ("modo interface"):** num slot de usuário, desligar TODOS os blocos
  (PREAMP ← principal, FX1, OD/DS, FX2, DELAY, REVERB, PEDAL FX). MENU → OUTPUT
  SELECT → LINE/PHONES (resposta plana). Salvar como "DI"/"PLUGIN". Assim o USB
  manda uma DI limpa e o Neural faz todo o tom.
- **Rotina ao ligar:** (1) MENU → USB → DIRECT MONITOR → OFF; (2) selecionar o
  patch DI; (3) abrir o plugin. Resultado: ouve só o plugin, com sinal limpo.

**Referência de endereços USB (área System, base `00 00 00 00`) — descobertos:**
`50`=USB I/O mode (só Normal na GT-1), `51`=input level (dB), `52`=EFX OUT level
(0–200%), `53`=MIX/retorno do PC (0–200%), `54`/`55`=on/off legíveis mas SEM
efeito audível. DIRECT MONITOR não tem endereço.

---

## 8. Referências

- **FxFloorBoard (editor open-source, tem versão GT-1 — fonte do mapa de endereços):**
  https://sourceforge.net/projects/fxfloorboard/
  https://sourceforge.net/projects/fxfloorboard/files/GT-1FxFloorBoard/
- **GT-100/GT-001 MIDI Implementation (mapa de endereços de referência):**
  https://static.roland.com/assets/media/pdf/GT-100_GT-001_MIDI_Imple_e01_W.pdf
- **GT-1 Service Notes (formato SysEx confirmado):**
  https://www.manualslib.com/manual/2485086/Boss-Gt-1.html
- **GT-1 Owner's Manuals / Parameter Guide:**
  https://www.boss.info/global/support/by_product/gt-1/owners_manuals/
  https://static.roland.com/assets/media/pdf/GT-1_parameter_eng02_W.pdf
- **Neural DSP — Setting up MIDI:**
  https://neuraldsp.com/getting-started/controlling-plugins-with-midi
- **Reverse engineering SysEx Roland/Boss (fórum):**
  https://www.vguitarforums.com/smf/index.php?topic=19707.25
- **loopMIDI (porta MIDI virtual, Windows):** Tobias Erichsen (loopMIDI).
- **USBPcap + Wireshark:** captura de tráfego USB MIDI.

---

## 9. Prompt sugerido para iniciar no Claude Code

> "Estou construindo uma ponte MIDI para a Boss GT-1. Leia o arquivo
> `HANDOFF_ponte_gt1.md` e o `ponte_gt1.py` nesta pasta para todo o contexto.
> Estamos no **Marco 1**: preciso rodar o probe e confirmar que o Python
> consegue ler um DT1 da GT-1. Me ajude a executar, interpretar a saída e, se
> funcionar, partir para o Marco 2 (descobrir o endereço do patch atual).
> Ambiente: Windows, GT-1 conectada por USB com driver Boss instalado."

---

_Resumo em uma linha: a GT-1 não manda MIDI, mas responde a polling SysEx
(Model ID `00 00 00 30`); a ponte pergunta o estado dela e traduz mudanças em
PC/CC numa porta virtual para o plugin. Falta achar os endereços a monitorar._
