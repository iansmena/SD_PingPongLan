# protocolo.py
# Contrato compartido entre servidor y cliente de Pong.
# Todo lo que AMBOS lados deben tener igual vive aquí:
#   1. Configuración (red, medidas y parámetros del juego)
#   2. Mensajes que viajan por la red (una clase por tipo de mensaje)
#   3. Codificación / decodificación (objeto <-> bytes)
#   4. Clase Conexion: envía y recibe mensajes completos sobre TCP
#
# IMPORTANTE: este archivo debe estar en la misma carpeta que servidor.py y
# cliente.py, y ser la MISMA versión en todas las máquinas. Si cambias los
# mensajes, sube VERSION para que un cliente desactualizado lo detecte.
import json
import socket
import threading
from dataclasses import asdict, dataclass, field, fields
from typing import ClassVar

# =====================================================================
# 1. CONFIGURACIÓN
# =====================================================================
VERSION = 1               # versión del protocolo: súbela si cambias los mensajes

# ---- Red ----
PUERTO = 5555
TAM_RECV = 4096           # bytes que se leen por cada recv()
MAX_BUFFER = 64 * 1024    # si llega más que esto sin un '\n', se corta la conexión

# ---- Medidas de la cancha (las usan servidor y cliente) ----
ANCHO, ALTO = 1280, 720
RADIO_PELOTA = 20
ANCHO_PALETA, ALTO_PALETA = 10, 120
MARGEN_PALETA = 50        # distancia del centro de cada paleta al borde lateral

ROLES = ("izq", "der")    # lados posibles de un jugador
# Coordenada x del centro de cada paleta, según el rol
X_PALETA = {"izq": MARGEN_PALETA, "der": ANCHO - MARGEN_PALETA}

# ---- Física (solo la usa el servidor, pero se ajusta desde aquí) ----
VEL_PALETA = 300          # px/s
VEL_INICIAL = 400         # rapidez inicial de la pelota (px/s)
VEL_MAXIMA = 1000         # tope para que no atraviese las paletas
ACELERACION = 1.05        # cada golpe en paleta sube la rapidez un 5 %
ANGULO_MAX = 60           # desvío máximo (grados) al golpear el borde de la paleta
ANGULO_SAQUE = 30         # el saque sale con un ángulo aleatorio entre ±30°
TICKS = 60                # actualizaciones de física por segundo
DT_MAXIMO = 0.05          # tope de dt para evitar saltos grandes

COLORES = ["white", "red", "orange", "yellow", "green",
           "cyan", "deepskyblue", "magenta", "hotpink"]


# =====================================================================
# 2. MENSAJES
# =====================================================================
# Cada tipo de mensaje es una clase. @dataclass genera automáticamente
# __init__, __repr__ y __eq__ a partir de los atributos declarados.
# TIPO es un ClassVar: pertenece a la clase, no a cada objeto, y por eso
# no se cuenta como campo del mensaje.

class Mensaje:
    """Clase base: sabe convertirse en diccionario listo para JSON."""
    TIPO: ClassVar[str] = ""

    def a_dict(self):
        # asdict() convierte los campos del dataclass en un diccionario
        return {"tipo": self.TIPO, **asdict(self)}


@dataclass
class Input(Mensaje):
    """Cliente -> Servidor: qué teclas está presionando el jugador."""
    TIPO: ClassVar[str] = "input"
    arriba: bool = False
    abajo: bool = False


@dataclass
class Bienvenida(Mensaje):
    """Servidor -> Cliente: lado asignado y versión del protocolo del servidor."""
    TIPO: ClassVar[str] = "bienvenida"
    rol: str = ""
    version: int = VERSION


@dataclass
class Lleno(Mensaje):
    """Servidor -> Cliente: ya hay dos jugadores, se rechaza la conexión."""
    TIPO: ClassVar[str] = "lleno"


@dataclass
class Estado(Mensaje):
    """Servidor -> Clientes: todo lo necesario para dibujar un frame."""
    TIPO: ClassVar[str] = "estado"
    jugando: bool = False
    pelota: list = field(default_factory=lambda: [ANCHO / 2, ALTO / 2])  # [x, y]
    color: str = "white"
    paletas: dict = field(default_factory=lambda: {r: ALTO / 2 for r in ROLES})  # rol -> y
    puntos: list = field(default_factory=lambda: [0, 0])  # [izq, der]


# Registro: "tipo" recibido -> clase que lo representa.
# Si creas un mensaje nuevo, agrégalo a esta tupla.
TIPOS = {cls.TIPO: cls for cls in (Input, Bienvenida, Lleno, Estado)}


# =====================================================================
# 3. CODIFICACIÓN
# =====================================================================
# Formato en el cable: un objeto JSON por línea, terminado en '\n'.
# El '\n' marca dónde termina cada mensaje dentro del flujo de bytes de TCP.

def codificar(mensaje):
    """Mensaje -> bytes listos para enviar."""
    # separators sin espacios = mensajes más cortos
    return (json.dumps(mensaje.a_dict(), separators=(",", ":")) + "\n").encode("utf-8")


def decodificar(linea):
    """bytes (una línea sin '\\n') -> objeto Mensaje. Lanza ValueError si es inválido."""
    datos = json.loads(linea)                 # JSONDecodeError es subclase de ValueError
    if not isinstance(datos, dict):
        raise ValueError("el mensaje no es un objeto JSON")
    cls = TIPOS.get(datos.pop("tipo", None))
    if cls is None:
        raise ValueError("tipo de mensaje desconocido")
    # Solo se pasan los campos que la clase conoce (ignora campos extra)
    validos = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in datos.items() if k in validos})


# =====================================================================
# 4. CONEXIÓN
# =====================================================================
class ConexionCerrada(Exception):
    """Se lanza cuando el otro extremo cierra la conexión o esta se rompe."""


class Conexion:
    """
    Envuelve un socket TCP y trabaja con MENSAJES en vez de bytes.
    Resuelve el problema de TCP de mensajes pegados o partidos con un buffer.
    """

    def __init__(self, sock):
        self.sock = sock
        self._buffer = b""
        # Si dos hilos envían por el mismo socket a la vez, sus bytes podrían
        # mezclarse. Este lock hace que cada mensaje salga completo.
        self._lock_envio = threading.Lock()
        # Desactiva el algoritmo de Nagle: cada mensaje sale al instante
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    @classmethod
    def conectar(cls, ip, puerto=PUERTO, timeout=5):
        """Lado cliente: abre la conexión con el servidor."""
        sock = socket.create_connection((ip, puerto), timeout=timeout)
        sock.settimeout(None)                 # luego, recv bloqueante normal
        return cls(sock)

    def enviar(self, mensaje):
        """Envía un mensaje completo. Lanza ConexionCerrada si falla."""
        datos = codificar(mensaje)
        try:
            with self._lock_envio:
                self.sock.sendall(datos)
        except OSError as e:
            raise ConexionCerrada(str(e)) from e

    def recibir(self):
        """Bloquea hasta tener un mensaje completo y lo devuelve como objeto."""
        while True:
            # Acumula bytes hasta que haya al menos una línea completa
            while b"\n" not in self._buffer:
                try:
                    datos = self.sock.recv(TAM_RECV)
                except OSError as e:
                    raise ConexionCerrada(str(e)) from e
                if not datos:                 # recv vacío = el otro lado cerró
                    raise ConexionCerrada("el otro extremo cerró la conexión")
                self._buffer += datos
                if len(self._buffer) > MAX_BUFFER:
                    raise ConexionCerrada("mensaje demasiado largo")

            linea, self._buffer = self._buffer.split(b"\n", 1)
            try:
                return decodificar(linea)
            except (ValueError, TypeError):
                continue                      # mensaje corrupto o desconocido: se ignora

    def cerrar(self):
        """Cierra la conexión. Se puede llamar varias veces sin error."""
        try:
            # shutdown despierta a un hilo que esté bloqueado en recv()
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass
