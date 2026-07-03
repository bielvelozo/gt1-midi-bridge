#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bridge.py - Ponte MIDI Boss GT-1 -> porta virtual (v2, robusta)

O que faz
---------
Faz polling do estado da GT-1 via SysEx (RQ1/DT1) e traduz mudancas em
mensagens MIDI padrao numa porta virtual (loopMIDI), que o plugin
(ex.: Neural DSP Archetype) escuta:

  - Troca de patch (footswitches de banco/patch) -> Program Change
  - CTL1 (on/off do efeito alvo)                 -> CC (toggle)
  - Pedal de expressao EXP1                      -> CC (0..127 continuo)

Robustez (Marco 5 - ao vivo)
----------------------------
  - config.json gerado na 1a execucao (canal, CCs, modo do CTL1, poll, portas).
  - Auto-reconexao: se a GT-1 cair, a ponte espera e volta sozinha.
  - Supervisor: um processo pai relanca a ponte se ela morrer (inclusive
    crash nativo ao remover o USB). Nunca fica "morta".
  - Log em arquivo (bridge.log) alem do console.
  - Empacota em .exe (PyInstaller). Usa python-rtmidi direto (sem mido),
    o que evita o travamento do mido ao abrir a entrada no .exe.

Enderecos (empiricos + fonte do FxFloorBoard, 2026-07-02)
---------------------------------------------------------
  00 01 00 00  (2 bytes)   NUMERO do patch atual (0-based; U01 = 0).
                           So responde com o "modo editor": DT1 7F 00 00 01 = 01.
  60 00 00 00  (16 bytes)  nome do patch atual (buffer temporario)
  60 00 11 19  (1 byte)    on/off do efeito controlado pelo CTL1 (0/1)
  60 00 06 33  (1 byte)    posicao do EXP1 (0x00..0x64 = 0..100)
  10 <N> 00 00 (16 bytes)  nome do patch de usuario U(N+1), N = 0..98

Requisitos
----------
  pip install python-rtmidi
  loopMIDI (Tobias Erichsen) com uma porta virtual, ex.: "GT1-Bridge"
  Boss Tone Studio / FxFloorBoard FECHADOS (a porta USB e exclusiva).
"""

import os
import sys
import json
import time
import logging
import threading
import subprocess
import rtmidi

# ==================== CONFIG PADRAO ====================
DEFAULT_CONFIG = {
    "gt1_port_hint": "GT-1",            # trecho do nome das portas da GT-1
    "virtual_port_hint": "GT1-Bridge",  # trecho do nome da porta virtual (loopMIDI)
    "midi_channel": 1,                  # canal MIDI de saida (1..16)
    "poll_hz": 20,                      # ciclos de leitura por segundo
    "ctl1": {
        "enabled": True,
        "cc": 80,                       # CC emitido pelo CTL1
        # Como o CTL1 vira MIDI (depende de como o controle do plugin reage):
        #   "trigger" -> 127 a cada pisada (controle alterna a cada mensagem)
        #   "switch"  -> 127 ao ligar, 0 ao desligar (controle SEGUE o valor)
        #   "pulse"   -> 127 e depois 0 a cada pisada (controle alterna na
        #                borda de subida, como um footswitch momentaneo)
        "mode": "trigger"
    },
    "exp1": {
        "enabled": True,
        "cc": 11                        # CC emitido pelo EXP1 (Expression)
    },
    "patch_change": {
        "enabled": True                 # emitir Program Change ao trocar patch
    },
    # Knobs 1/2/3 como controladores MIDI. Cada um le um parametro (endereco)
    # que voce atribuiu ao knob no MENU->KNOB SETTING (de um efeito OFF, pra
    # nao afetar o som). "max" = valor cheio do parametro (LEVEL = 100).
    # Para achar o endereco de um knob novo: rode o diagnostico e gire o knob.
    "knobs": [
        {"enabled": True, "name": "knob1", "cc": 12, "address": "60 00 10 1D", "max": 100},
        {"enabled": True, "name": "knob2", "cc": 13, "address": "60 00 06 18", "max": 100},
        {"enabled": True, "name": "knob3", "cc": 14, "address": "60 00 10 74", "max": 100}
    ],
    "reconnect_seconds": 2.0            # espera entre tentativas de reconexao
}
# ======================================================

MODEL_ID = [0x00, 0x00, 0x00, 0x30]   # GT-1
DEV_ID, RQ1, DT1 = 0x7F, 0x11, 0x12

ADDR_PATCH_NUM = [0x00, 0x01, 0x00, 0x00]   # numero do patch atual (2 bytes)
ADDR_TEMP_NAME = [0x60, 0x00, 0x00, 0x00]   # nome do patch atual (16 bytes)
ADDR_CTL1      = [0x60, 0x00, 0x00, 0x5B]   # efeito do CTL1 on/off (1 byte, CTL1=SYSTEM)
ADDR_EXP       = [0x60, 0x00, 0x06, 0x33]   # posicao do EXP1 (1 byte, 0..100)
ADDR_EDITOR    = [0x7F, 0x00, 0x00, 0x01]   # "modo editor" (escrever 01)

SIZE_WORD = [0x00, 0x00, 0x00, 0x02]
SIZE_BYTE = [0x00, 0x00, 0x00, 0x01]
SIZE_NAME = [0x00, 0x00, 0x00, 0x10]

log = logging.getLogger("bridge")


class ReconnectNeeded(Exception):
    """Sinaliza que a GT-1 sumiu e a ponte deve reconectar."""


# ---------- utilitarios ----------

def base_dir():
    """Pasta do .exe (quando congelado) ou do script."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def load_config():
    path = os.path.join(base_dir(), "config.json")
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(DEFAULT_CONFIG, handle, indent=2, ensure_ascii=False)
        log.info("config.json criado com valores padrao em %s", path)
        return dict(DEFAULT_CONFIG)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            user_config = json.load(handle)
    except Exception as error:
        log.warning("config.json invalido (%s); usando padrao.", error)
        return dict(DEFAULT_CONFIG)
    merged = dict(DEFAULT_CONFIG)
    merged.update(user_config)
    for key in ("ctl1", "exp1", "patch_change"):
        section = dict(DEFAULT_CONFIG[key])
        section.update(user_config.get(key, {}))
        merged[key] = section
    return merged


def roland_checksum(payload):
    return (0x80 - (sum(payload) & 0x7F)) & 0x7F


def rq1_body(addr, size):
    """Bytes ENTRE F0 e F7 de um pedido de leitura (RQ1)."""
    body = addr + size
    return [0x41, DEV_ID] + MODEL_ID + [RQ1] + body + [roland_checksum(body)]


def dt1_body(addr, values):
    """Bytes ENTRE F0 e F7 de uma escrita (DT1)."""
    body = addr + values
    return [0x41, DEV_ID] + MODEL_ID + [DT1] + body + [roland_checksum(body)]


def sysex(body_bytes):
    """Mensagem SysEx completa (com F0/F7) para o rtmidi."""
    return [0xF0] + body_bytes + [0xF7]


def to_text(raw_bytes):
    return "".join(chr(b) if 0x20 <= b < 0x7F else "?" for b in raw_bytes).rstrip()


def find_port_index(port_names, hint):
    for index, name in enumerate(port_names):
        if hint.lower() in name.lower():
            return index, name
    return None, None


def parse_addr(text):
    """Converte "60 00 10 1D" -> [0x60, 0x00, 0x10, 0x1D]."""
    return [int(part, 16) for part in text.split()]


# ---------- a ponte ----------

class Bridge:
    def __init__(self, config):
        self.config = config
        self.channel = max(0, min(15, int(config["midi_channel"]) - 1))
        self.midi_in = None
        self.midi_out = None
        self.virtual_out = None
        self.gt_out_index = None
        # knobs: pre-processa enderecos e prepara o estado de cada um
        self.knobs = []
        for knob in config.get("knobs", []):
            if not knob.get("enabled", True):
                continue
            try:
                addr = parse_addr(knob["address"])
            except Exception:
                log.warning("knob '%s' com address invalido: %r",
                            knob.get("name", "?"), knob.get("address"))
                continue
            self.knobs.append({
                "name": knob.get("name", "knob"),
                "cc": int(knob["cc"]),
                "addr": addr,
                "addr_tuple": tuple(addr),
                "max": max(1, int(knob.get("max", 100))),
                "last": None,
            })

    # -- ciclo de vida da conexao --

    def connect(self):
        """Abre as portas e prepara a GT-1. Levanta ReconnectNeeded se faltar
        a GT-1; RuntimeError se faltar a porta virtual (erro de setup)."""
        log.info("procurando portas MIDI...")
        probe_in, probe_out = rtmidi.MidiIn(), rtmidi.MidiOut()
        in_names = probe_in.get_ports()
        out_names = probe_out.get_ports()
        probe_in.delete()
        probe_out.delete()
        log.info("portas: %d entradas, %d saidas", len(in_names), len(out_names))

        gt_in_index, gt_in_name = find_port_index(in_names, self.config["gt1_port_hint"])
        gt_out_index, gt_out_name = find_port_index(out_names, self.config["gt1_port_hint"])
        virtual_index, virtual_name = find_port_index(out_names, self.config["virtual_port_hint"])

        if virtual_index is None:
            raise RuntimeError(
                f"porta virtual '{self.config['virtual_port_hint']}' nao encontrada "
                "(o loopMIDI esta aberto e com a porta criada?)"
            )
        if gt_in_index is None or gt_out_index is None:
            raise ReconnectNeeded("porta da GT-1 nao encontrada")

        self.midi_in = rtmidi.MidiIn()
        # IMPORTANTE: por padrao o rtmidi IGNORA SysEx. Precisamos dele.
        self.midi_in.ignore_types(sysex=False, timing=True, active_sense=True)
        self.midi_in.open_port(gt_in_index)

        self.midi_out = rtmidi.MidiOut()
        self.midi_out.open_port(gt_out_index)
        self.gt_out_index = gt_out_index

        if self.virtual_out is None:
            self.virtual_out = rtmidi.MidiOut()
            self.virtual_out.open_port(virtual_index)

        log.info("GT-1 IN=%s  OUT=%s  Virtual=%s", gt_in_name, gt_out_name, virtual_name)
        self.enable_editor_mode()
        log.info("conectada. Opere a GT-1.")

    def close(self, keep_virtual=True):
        for port in (self.midi_in, self.midi_out):
            try:
                if port is not None:
                    port.close_port()
                    port.delete()
            except Exception:
                pass
        self.midi_in = self.midi_out = None
        if not keep_virtual and self.virtual_out is not None:
            try:
                self.virtual_out.close_port()
                self.virtual_out.delete()
            except Exception:
                pass
            self.virtual_out = None

    # -- I/O com a GT-1 --

    def send_gt(self, body_bytes):
        """Envio robusto (o WinMM falha esporadicamente ao mandar SysEx)."""
        message = sysex(body_bytes)
        for attempt in range(5):
            try:
                self.midi_out.send_message(message)
                return
            except Exception:
                time.sleep(0.04 * (attempt + 1))
                if attempt == 2:
                    try:
                        self.midi_out.close_port()
                        self.midi_out.delete()
                    except Exception:
                        pass
                    time.sleep(0.15)
                    try:
                        self.midi_out = rtmidi.MidiOut()
                        self.midi_out.open_port(self.gt_out_index)
                    except Exception:
                        pass
        raise ReconnectNeeded("falha ao enviar para a GT-1")

    def drain_dt1(self):
        """Coleta os DT1 recebidos -> lista de (addr_tupla, bytes de dados).
        Se a porta cair, converte em ReconnectNeeded."""
        results = []
        try:
            while True:
                item = self.midi_in.get_message()
                if item is None:
                    break
                data, _delta = item
                if len(data) < 2 or data[0] != 0xF0 or data[-1] != 0xF7:
                    continue
                raw = data[1:-1]  # bytes entre F0 e F7
                if len(raw) < 12 or raw[0] != 0x41 or raw[6] != DT1:
                    continue
                results.append((tuple(raw[7:11]), raw[11:-1]))
        except Exception as error:
            raise ReconnectNeeded(f"leitura da GT-1 falhou: {error}")
        return results

    def enable_editor_mode(self):
        """Sem isso, o registrador 00 01 00 00 nao responde a RQ1."""
        self.send_gt(dt1_body(ADDR_EDITOR, [0x01]))
        time.sleep(0.2)

    # -- saida para a porta virtual (para o plugin) --

    def send_virtual(self, message_bytes):
        try:
            self.virtual_out.send_message(message_bytes)
        except Exception as error:
            raise RuntimeError(f"falha ao enviar para a porta virtual: {error}")

    def program_change(self, program):
        self.send_virtual([0xC0 | self.channel, program & 0x7F])

    def control_change(self, control, value):
        self.send_virtual([0xB0 | self.channel, control & 0x7F, value & 0x7F])

    # -- laco principal de polling --

    def poll_loop(self):
        config = self.config
        period = 1.0 / max(1, int(config["poll_hz"]))
        ctl1_cfg, exp1_cfg, patch_cfg = config["ctl1"], config["exp1"], config["patch_change"]

        last_program = last_ctl1 = last_exp = None
        current_name = "?"
        silent_cycles = 0
        silence_limit = max(10, int(config["poll_hz"]) * 3)  # ~3s sem resposta

        knob_desc = "".join(f" | {kb['name']} CC{kb['cc']}" for kb in self.knobs)
        log.info("Ponte ativa a %s Hz | canal %d | CTL1 CC%s (%s) | EXP1 CC%s%s",
                 config["poll_hz"], self.channel + 1,
                 ctl1_cfg["cc"], ctl1_cfg["mode"], exp1_cfg["cc"], knob_desc)

        knob_addrs = {knob["addr_tuple"]: knob for knob in self.knobs}

        while True:
            self.send_gt(rq1_body(ADDR_PATCH_NUM, SIZE_WORD))
            self.send_gt(rq1_body(ADDR_TEMP_NAME, SIZE_NAME))
            if ctl1_cfg["enabled"]:
                self.send_gt(rq1_body(ADDR_CTL1, SIZE_BYTE))
            if exp1_cfg["enabled"]:
                self.send_gt(rq1_body(ADDR_EXP, SIZE_BYTE))
            for knob in self.knobs:
                self.send_gt(rq1_body(knob["addr"], SIZE_BYTE))
            time.sleep(period)

            patch_value = ctl1_value = exp_value = None
            knob_values = {}
            got_any = False
            for raddr, value in self.drain_dt1():
                got_any = True
                if raddr == tuple(ADDR_PATCH_NUM) and len(value) >= 2:
                    patch_value = value[0] * 128 + value[1]
                elif raddr == tuple(ADDR_TEMP_NAME) and len(value) >= 16:
                    current_name = to_text(value[:16])
                elif raddr == tuple(ADDR_CTL1) and value:
                    ctl1_value = value[0]
                elif raddr == tuple(ADDR_EXP) and value:
                    exp_value = value[0]
                elif raddr in knob_addrs and value:
                    knob_values[raddr] = value[0]

            silent_cycles = 0 if got_any else silent_cycles + 1
            if silent_cycles >= silence_limit:
                raise ReconnectNeeded("GT-1 parou de responder")

            # --- troca de patch -> Program Change ---
            if patch_cfg["enabled"] and patch_value is not None and patch_value != last_program:
                if last_program is not None and patch_value <= 127:
                    self.program_change(patch_value)
                label = f"U{patch_value + 1:02d}" if patch_value <= 98 else f"#{patch_value}"
                suffix = "" if patch_value <= 127 else "  (fora do range MIDI, PC nao enviado)"
                log.info("[patch] %s '%s' -> PC %d%s", label, current_name, patch_value, suffix)
                last_program = patch_value

            # --- CTL1 -> CC ---
            if ctl1_cfg["enabled"] and ctl1_value is not None and ctl1_value != last_ctl1:
                if last_ctl1 is not None:
                    mode = ctl1_cfg["mode"]
                    if mode == "pulse":
                        # pulso 127->0 a cada pisada: emula o aperto de um
                        # footswitch (para controles que alternam na borda de
                        # subida e precisam do 0 pra "soltar")
                        self.control_change(ctl1_cfg["cc"], 127)
                        self.control_change(ctl1_cfg["cc"], 0)
                        log.info("[ctl1 ] %s -> CC%d pulso 127/0",
                                 "ON" if ctl1_value else "OFF", ctl1_cfg["cc"])
                    else:
                        if mode == "trigger":
                            cc_value = 127            # 127 a cada pisada
                        else:                          # "switch": segue o estado
                            cc_value = 127 if ctl1_value else 0
                        self.control_change(ctl1_cfg["cc"], cc_value)
                        log.info("[ctl1 ] %s -> CC%d=%d",
                                 "ON" if ctl1_value else "OFF", ctl1_cfg["cc"], cc_value)
                last_ctl1 = ctl1_value

            # --- EXP1 -> CC continuo ---
            if exp1_cfg["enabled"] and exp_value is not None and exp_value != last_exp:
                if last_exp is not None:
                    cc_value = min(127, (exp_value * 127 + 50) // 100)
                    self.control_change(exp1_cfg["cc"], cc_value)
                    log.info("[exp1 ] %3d -> CC%d=%d", exp_value, exp1_cfg["cc"], cc_value)
                last_exp = exp_value

            # --- knobs -> CC continuo ---
            for knob in self.knobs:
                raw = knob_values.get(knob["addr_tuple"])
                if raw is None or raw == knob["last"]:
                    continue
                if knob["last"] is not None:
                    cc_value = min(127, (raw * 127 + knob["max"] // 2) // knob["max"])
                    self.control_change(knob["cc"], cc_value)
                    log.info("[%-5s] %3d -> CC%d=%d", knob["name"], raw, knob["cc"], cc_value)
                knob["last"] = raw


def setup_logging():
    log.setLevel(logging.INFO)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
    log.addHandler(console)
    try:
        file_handler = logging.FileHandler(os.path.join(base_dir(), "bridge.log"), encoding="utf-8")
        file_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        log.addHandler(file_handler)
    except Exception:
        pass  # sem log em arquivo nao e fatal


def run():
    config = load_config()
    bridge = Bridge(config)
    retry = float(config.get("reconnect_seconds", 2.0))
    announced_wait = False

    try:
        while True:
            try:
                bridge.connect()
                announced_wait = False
                bridge.poll_loop()
            except ReconnectNeeded as reason:
                if not announced_wait:
                    log.warning("Reconectando (%s)... ligue/conecte a GT-1.", reason)
                    announced_wait = True
                bridge.close(keep_virtual=True)
                time.sleep(retry)
            except RuntimeError as error:
                if not announced_wait:
                    log.error("%s", error)
                    announced_wait = True
                bridge.close(keep_virtual=False)
                time.sleep(max(retry, 3.0))
            except KeyboardInterrupt:
                raise
            except Exception as error:
                if not announced_wait:
                    log.warning("Falha inesperada (%s); reconectando...", error)
                    announced_wait = True
                bridge.close(keep_virtual=True)
                time.sleep(retry)
    finally:
        # fecha as portas ao sair (evita deixar a porta WinMM em estado travado)
        bridge.close(keep_virtual=False)


def start_parent_watch():
    """No processo-filho: vigia o pai pela stdin (pipe). Se o pai morrer, a
    stdin fecha (EOF) e o filho sai sozinho, evitando orfao segurando a porta."""
    def watch():
        try:
            while True:
                line = sys.stdin.readline()
                if line == "":  # EOF -> o supervisor morreu
                    break
        except Exception:
            pass
        # pede ao thread principal para sair limpo (fecha as portas via finally);
        # se ele estiver preso, o os._exit abaixo garante o encerramento.
        try:
            import _thread
            _thread.interrupt_main()
        except Exception:
            pass
        time.sleep(1.5)
        os._exit(0)
    threading.Thread(target=watch, daemon=True).start()


def worker_main():
    """O processo que realmente faz a ponte."""
    setup_logging()
    start_parent_watch()
    log.info("=== Ponte MIDI Boss GT-1 (v2) ===")
    try:
        run()
    except KeyboardInterrupt:
        log.info("Encerrado pelo usuario.")


def supervisor_main():
    """Relanca a ponte se ela cair (crash nativo, USB removido, etc.)."""
    if getattr(sys, "frozen", False):
        child_cmd = [sys.executable, "--worker"]
    else:
        child_cmd = [sys.executable, os.path.abspath(__file__), "--worker"]

    child_env = os.environ.copy()
    for key in list(child_env):
        if key.startswith("_MEI") or key.startswith("_PYI"):
            child_env.pop(key, None)

    print("=== Ponte Boss GT-1 (supervisor) — relanca a ponte se ela cair. "
          "Ctrl+C para sair. ===", flush=True)
    while True:
        proc = subprocess.Popen(child_cmd, stdin=subprocess.PIPE, env=child_env)
        try:
            while proc.poll() is None:
                time.sleep(0.3)
        except KeyboardInterrupt:
            try:
                proc.terminate()
            except Exception:
                pass
            print("[supervisor] encerrado.", flush=True)
            return
        print(f"[supervisor] a ponte caiu (code={proc.returncode}); "
              "reiniciando em 2s...", flush=True)
        try:
            time.sleep(2.0)
        except KeyboardInterrupt:
            return


def main():
    if "--worker" in sys.argv:
        worker_main()
    else:
        supervisor_main()


if __name__ == "__main__":
    main()
