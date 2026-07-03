#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sysex_probe.py  -  Ponte MIDI para Boss GT-1  (v0: teste de comunicacao)

CONTEXTO
--------
A GT-1 nao envia CC/PC utilizavel pelos footswitches, mas RESPONDE por SysEx
quando e perguntada (e o que o Boss Tone Studio e o FxFloorBoard fazem).
A ideia da "ponte" e: perguntar o estado da GT-1 varias vezes por segundo,
detectar mudancas (troca de patch, efeito on/off, pedal) e emitir
Program Change / Control Change numa porta MIDI virtual que o plugin escuta.

Este arquivo e a VERSAO 0: ele so PROVA que o Python consegue mandar uma
pergunta (RQ1) e receber a resposta (DT1) da GT-1. Depois que confirmarmos
isso e descobrirmos os enderecos certos, a gente adiciona a saida de PC/CC.

PROTOCOLO (confirmado nas Service Notes oficiais da GT-1)
---------------------------------------------------------
    F0 41 7F 00 00 00 30 11 <addr:4> <size:4> <checksum> F7
      41           -> Roland (Manufacturer ID)
      7F           -> Device ID (broadcast)
      00 00 00 30  -> Model ID da GT-1
      11 = RQ1 (pedido de leitura)   |   12 = DT1 (resposta com dados)
    checksum Roland = (0x80 - (soma(addr+size) & 0x7F)) & 0x7F

REQUISITOS
----------
    pip install mido python-rtmidi
    (opcional, para a ponte final) loopMIDI com uma porta virtual, ex: "GT1-Bridge"

USO
---
    1) Feche o Boss Tone Studio e o FxFloorBoard (so 1 app por vez usa a porta).
    2) python sysex_probe.py
       -> manda RQ1 pro endereco 00 00 00 00 e mostra a resposta.

    Para testar OUTRO endereco (util para caca ao "patch atual"), passe
    8 bytes em hex (4 de endereco + 4 de tamanho):
       python sysex_probe.py 00 00 00 00 00 00 00 10
"""

import sys
import time
import mido

# ==================== CONFIG ====================
GT1_HINT = "GT-1"        # trecho do nome da porta da GT-1
POLL_HZ  = 25            # perguntas por segundo

MODEL_ID = [0x00, 0x00, 0x00, 0x30]   # GT-1
DEV_ID   = 0x7F
RQ1      = 0x11
DT1      = 0x12

# Endereco/tamanho padrao do teste (area de sistema). Pode sobrescrever via linha de comando.
DEFAULT_ADDR = [0x00, 0x00, 0x00, 0x00]
DEFAULT_SIZE = [0x00, 0x00, 0x00, 0x01]
# ================================================


def roland_checksum(payload):
    """Checksum padrao Roland sobre address+size (ou address+data)."""
    return (0x80 - (sum(payload) & 0x7F)) & 0x7F


def rq1_sysex_data(addr, size):
    """Bytes ENTRE F0 e F7 (formato que o mido espera em Message('sysex', data=...))."""
    body = addr + size
    return [0x41, DEV_ID] + MODEL_ID + [RQ1] + body + [roland_checksum(body)]


def pick(names, hint):
    for n in names:
        if hint.lower() in n.lower():
            return n
    return None


def parse_cli_address():
    args = sys.argv[1:]
    if not args:
        return DEFAULT_ADDR, DEFAULT_SIZE
    if len(args) != 8:
        print("Passe exatamente 8 bytes hex: 4 de endereco + 4 de tamanho.")
        sys.exit(1)
    vals = [int(a, 16) for a in args]
    return vals[:4], vals[4:]


def hexs(bs):
    return " ".join(f"{b:02X}" for b in bs)


def main():
    addr, size = parse_cli_address()

    ins  = mido.get_input_names()
    outs = mido.get_output_names()
    print("Entradas MIDI:", ins)
    print("Saidas  MIDI :", outs)

    gt_in_name  = pick(ins, GT1_HINT)
    gt_out_name = pick(outs, GT1_HINT)
    if not gt_in_name or not gt_out_name:
        print("\n[ERRO] Nao achei a porta da GT-1.")
        print("       Ela esta ligada, conectada por USB e SEM outro app usando (Tone Studio/FxFloorBoard fechados)?")
        sys.exit(1)

    print(f"\nGT-1 IN : {gt_in_name}")
    print(f"GT-1 OUT: {gt_out_name}")

    gt_in  = mido.open_input(gt_in_name)
    gt_out = mido.open_output(gt_out_name)

    data = rq1_sysex_data(addr, size)
    print(f"\nLendo endereco [{hexs(addr)}] tamanho [{hexs(size)}]")
    print("RQ1 enviado:", hexs([0xF0] + data + [0xF7]))
    print("\nAguardando DT1... troque de patch / pise nos pedais para ver o valor mudar.")
    print("(Ctrl+C para sair)\n")

    period = 1.0 / POLL_HZ
    last = None
    got_any = False
    while True:
        gt_out.send(mido.Message('sysex', data=data))
        for msg in gt_in.iter_pending():
            if msg.type == 'sysex':
                raw = list(msg.data)  # ja vem sem F0/F7
                # esperado: 41 7F 00 00 00 30 12 <addr:4> <valor...> <chk>
                if len(raw) >= 8 and raw[0] == 0x41 and raw[6] == DT1:
                    got_any = True
                    raddr = raw[7:11]
                    value = raw[11:-1]  # tira o checksum
                    if value != last:
                        last = value
                        print(f"DT1  addr[{hexs(raddr)}]  valor[{hexs(value)}]")
        time.sleep(period)

    # (nunca chega aqui; laco infinito)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nEncerrado.")
