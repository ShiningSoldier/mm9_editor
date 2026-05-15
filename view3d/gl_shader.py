"""
gl_shader.py
============

Embedded GLSL source strings and compile/link helpers.

The GLSL strings are module-level constants and are always importable
regardless of whether PyOpenGL is installed.  The compile_shader() and
link_program() functions use GL calls and must only be called once a
valid GL context exists.

Two shader programs are defined:

  SOLID    — renders BSP geometry: interleaved vec3 position + vec3 normal,
             flat-shaded with a single directional light and a per-model
             category tint colour.  Optional linear depth fog: set uFogEnabled=1
             and provide uFogNear / uFogFar / uFogColor.

  BILLBOARD — three-stage (vert + geom + frag) program that renders each
              WorldObject as a camera-facing quad sized in world units.
              The vertex shader passes the world-space position through;
              the geometry shader (BILLBOARD_GEOM) expands each GL_POINTS
              vertex into a triangle-strip quad facing the camera using
              uCamPos + uWorldSize uniforms; the fragment shader clips to
              a circle and handles selection highlights.
              Also used for the colour-encoded picking pass (uPickMode=1).
              Use ShaderProgram.build3(BILLBOARD_VERT, BILLBOARD_GEOM,
              BILLBOARD_FRAG) to compile this pipeline.
"""

from __future__ import annotations

from typing import Sequence, Tuple


# ---------------------------------------------------------------------------
# SOLID shader — BSP geometry
# ---------------------------------------------------------------------------

SOLID_VERT = """
#version 330 core

layout(location = 0) in vec3 aPos;
layout(location = 1) in vec3 aNormal;
layout(location = 2) in vec2 aUV;

uniform mat4 uMVP;

out vec3  vNormal;
out float vEyeDist;   // eye-space depth  (clip.w = -z_eye, positive in front)
out vec2  vUV;        // texture coordinates from BSP surface planar projection

void main() {
    vec4 clip    = uMVP * vec4(aPos, 1.0);
    gl_Position  = clip;
    vNormal      = aNormal;          // already in world space (no non-uniform scale)
    vEyeDist     = clip.w;           // equals eye-space distance; no extra matrix needed
    vUV          = aUV;
}
""".strip()

SOLID_FRAG = """
#version 330 core

in  vec3  vNormal;
in  float vEyeDist;
in  vec2  vUV;
out vec4  FragColor;

uniform vec3      uColor;       // category tint used when uHasTex == 0
uniform vec3      uLightDir;    // normalised direction *toward* the light source
uniform float     uAlpha;       // overall opacity (1.0 = opaque)

// Texture sampling (Stage 3)
uniform sampler2D uTex;         // bound texture (texture unit 0)
uniform int       uHasTex;      // 1 = sample uTex, 0 = use uColor tint
uniform int       uUseTexAlpha; // 1 = honour texture alpha/cutout, 0 = opaque

// Depth fog
uniform int   uFogEnabled;  // 0 = off, 1 = on
uniform float uFogNear;     // eye-space distance where fog starts
uniform float uFogFar;      // eye-space distance where fog is fully opaque
uniform vec3  uFogColor;    // fog / sky colour (matches background clear colour)

void main() {
    // Base colour + alpha: textured or solid category tint
    vec4  texSample = (uHasTex == 1) ? texture(uTex, vUV) : vec4(uColor, 1.0);
    vec3  baseColor = texSample.rgb;
    float texAlpha  = texSample.a;

    // Cutout transparency: discard nearly-invisible fragments.
    // Handles foliage, iron fences, and other clip-alpha surfaces.
    // Semi-transparent fragments (alpha >= 0.1) are blended by the
    // pipeline (GL_BLEND is enabled in initgl).
    if (uHasTex == 1 && uUseTexAlpha == 1 && texAlpha < 0.1) discard;

    vec3  n        = normalize(vNormal);
    float diffuse  = max(dot(n, normalize(uLightDir)), 0.0);
    float light    = 0.25 + diffuse * 0.75;       // ambient + diffuse
    vec3  litColor = baseColor * light;

    if (uFogEnabled == 1) {
        float fogFactor = clamp((uFogFar - vEyeDist) / (uFogFar - uFogNear),
                                0.0, 1.0);
        litColor = mix(uFogColor, litColor, fogFactor);
    }

    // Multiply texture alpha by the per-model opacity uniform
    float outAlpha = (uHasTex == 1 && uUseTexAlpha == 1) ? texAlpha : 1.0;
    FragColor = vec4(litColor, outAlpha * uAlpha);
}
""".strip()


# ---------------------------------------------------------------------------
# BILLBOARD shader — WorldObject camera-facing quad sprites
# Three stages: VERT (pass-through) → GEOM (expand to quad) → FRAG (circle)
# ---------------------------------------------------------------------------

BILLBOARD_VERT = """
#version 330 core

layout(location = 0) in vec3 aPos;
layout(location = 1) in vec3 aColor;

out vec3 gColor;   // forwarded to geometry stage

void main() {
    // Pass world-space position through unmodified.
    // The geometry shader handles projection via uMVP.
    gl_Position = vec4(aPos, 1.0);
    gColor      = aColor;
}
""".strip()

BILLBOARD_GEOM = """
#version 330 core

layout(points)                           in;
layout(triangle_strip, max_vertices = 4) out;

uniform mat4  uMVP;
uniform vec3  uCamPos;    // camera eye in world space
uniform float uWorldSize; // sprite diameter in world units

in  vec3 gColor[];
out vec3 fColor;
out vec2 fUV;   // [-1,+1]; circle clip: discard if dot(fUV,fUV) > 1.0

void main() {
    vec3 center = gl_in[0].gl_Position.xyz;

    // Bail silently if camera is inside the sprite (degenerate)
    vec3  toEye = uCamPos - center;
    float dist  = length(toEye);
    if (dist < 0.001) return;
    toEye = toEye / dist;

    // Camera-facing billboard axes
    vec3 worldUp  = vec3(0.0, 1.0, 0.0);
    vec3 rawRight = cross(worldUp, toEye);
    float rlen    = length(rawRight);
    vec3 right    = (rlen > 0.001) ? rawRight / rlen : vec3(1.0, 0.0, 0.0);
    vec3 up       = normalize(cross(toEye, right));

    vec3 r = right * (uWorldSize * 0.5);
    vec3 u = up    * (uWorldSize * 0.5);

    fColor = gColor[0];
    gl_Position = uMVP * vec4(center - r - u, 1.0); fUV = vec2(-1.0, -1.0); EmitVertex();
    gl_Position = uMVP * vec4(center + r - u, 1.0); fUV = vec2( 1.0, -1.0); EmitVertex();
    gl_Position = uMVP * vec4(center - r + u, 1.0); fUV = vec2(-1.0,  1.0); EmitVertex();
    gl_Position = uMVP * vec4(center + r + u, 1.0); fUV = vec2( 1.0,  1.0); EmitVertex();
    EndPrimitive();
}
""".strip()

BILLBOARD_FRAG = """
#version 330 core

in  vec3 fColor;
in  vec2 fUV;
out vec4 FragColor;

uniform int uPickMode;    // 0 = normal render, 1 = picking pass
uniform int uSelected;    // world-index of the selected object (-1 = none)
uniform int uObjectIndex; // world-index of the current sprite

void main() {
    // Circular clip: fUV is in [-1,+1]; unit circle → r² > 1 means outside
    float r2 = dot(fUV, fUV);
    if (r2 > 1.0) discard;

    if (uPickMode == 1) {
        // Encode world-index as RGB: 8 bits each (supports up to 16 M objects)
        int   idx = uObjectIndex;
        float r   = float((idx      ) & 0xFF) / 255.0;
        float g   = float((idx >>  8) & 0xFF) / 255.0;
        float b   = float((idx >> 16) & 0xFF) / 255.0;
        FragColor = vec4(r, g, b, 1.0);
        return;
    }

    // Selection ring: white outline in the outer 30 % of the radius
    if (uObjectIndex == uSelected && r2 > 0.50) {
        FragColor = vec4(1.0, 1.0, 1.0, 1.0);
        return;
    }

    // Slight centre highlight for depth cue
    float shade = 0.75 + 0.25 * (1.0 - r2);
    FragColor = vec4(fColor * shade, 1.0);
}
""".strip()


# ---------------------------------------------------------------------------
# Compiler / linker  (require a live GL context)
# ---------------------------------------------------------------------------

def compile_shader(source: str, shader_type: int) -> int:
    """
    Compile a GLSL shader.  Returns the shader object id.
    Raises RuntimeError if compilation fails.

    Parameters
    ----------
    source      : GLSL source string
    shader_type : GL_VERTEX_SHADER or GL_FRAGMENT_SHADER
    """
    from OpenGL import GL  # type: ignore
    shader = GL.glCreateShader(shader_type)
    GL.glShaderSource(shader, source)
    GL.glCompileShader(shader)
    if not GL.glGetShaderiv(shader, GL.GL_COMPILE_STATUS):
        log = GL.glGetShaderInfoLog(shader).decode("utf-8", errors="replace")
        GL.glDeleteShader(shader)
        kind = "vertex" if shader_type == GL.GL_VERTEX_SHADER else "fragment"
        raise RuntimeError(f"GLSL {kind} compile error:\n{log}")
    return shader


def link_program(vert_src: str, frag_src: str) -> int:
    """
    Compile *vert_src* + *frag_src* and link them into a GL program.
    Returns the program object id.  Raises RuntimeError on failure.
    """
    from OpenGL import GL  # type: ignore
    vert = compile_shader(vert_src, GL.GL_VERTEX_SHADER)
    frag = compile_shader(frag_src, GL.GL_FRAGMENT_SHADER)
    prog = GL.glCreateProgram()
    GL.glAttachShader(prog, vert)
    GL.glAttachShader(prog, frag)
    GL.glLinkProgram(prog)
    GL.glDeleteShader(vert)
    GL.glDeleteShader(frag)
    if not GL.glGetProgramiv(prog, GL.GL_LINK_STATUS):
        log = GL.glGetProgramInfoLog(prog).decode("utf-8", errors="replace")
        GL.glDeleteProgram(prog)
        raise RuntimeError(f"GLSL link error:\n{log}")
    return prog


def link_program3(vert_src: str, geom_src: str, frag_src: str) -> int:
    """
    Compile *vert_src* + *geom_src* + *frag_src* and link them into a GL
    program.  Returns the program object id.  Raises RuntimeError on failure.
    Requires OpenGL 3.2+ (geometry shaders).
    """
    from OpenGL import GL  # type: ignore
    vert = compile_shader(vert_src, GL.GL_VERTEX_SHADER)
    geom = compile_shader(geom_src, GL.GL_GEOMETRY_SHADER)
    frag = compile_shader(frag_src, GL.GL_FRAGMENT_SHADER)
    prog = GL.glCreateProgram()
    GL.glAttachShader(prog, vert)
    GL.glAttachShader(prog, geom)
    GL.glAttachShader(prog, frag)
    GL.glLinkProgram(prog)
    GL.glDeleteShader(vert)
    GL.glDeleteShader(geom)
    GL.glDeleteShader(frag)
    if not GL.glGetProgramiv(prog, GL.GL_LINK_STATUS):
        log = GL.glGetProgramInfoLog(prog).decode("utf-8", errors="replace")
        GL.glDeleteProgram(prog)
        raise RuntimeError(f"GLSL link error:\n{log}")
    return prog


# ---------------------------------------------------------------------------
# ShaderProgram helper
# ---------------------------------------------------------------------------

class ShaderProgram:
    """
    Thin wrapper around a linked GL program.

    Usage::

        prog = ShaderProgram.build(SOLID_VERT, SOLID_FRAG)
        with prog:
            prog.set_mat4("uMVP", mvp_array)
            prog.set_vec3("uColor", (0.4, 0.6, 0.9))
            # ... draw calls ...
    """

    def __init__(self, program_id: int) -> None:
        self._id   = program_id
        self._locs: dict = {}

    @classmethod
    def build(cls, vert_src: str, frag_src: str) -> "ShaderProgram":
        return cls(link_program(vert_src, frag_src))

    @classmethod
    def build3(cls, vert_src: str, geom_src: str, frag_src: str) -> "ShaderProgram":
        """Build a three-stage pipeline: vertex → geometry → fragment."""
        return cls(link_program3(vert_src, geom_src, frag_src))

    def __enter__(self) -> "ShaderProgram":
        from OpenGL import GL
        GL.glUseProgram(self._id)
        return self

    def __exit__(self, *_) -> None:
        from OpenGL import GL
        GL.glUseProgram(0)

    def use(self) -> None:
        from OpenGL import GL
        GL.glUseProgram(self._id)

    def _loc(self, name: str) -> int:
        if name not in self._locs:
            from OpenGL import GL
            self._locs[name] = GL.glGetUniformLocation(self._id, name)
        return self._locs[name]

    def set_mat4(self, name: str, mat: "np.ndarray") -> None:  # type: ignore[name-defined]
        from OpenGL import GL
        GL.glUniformMatrix4fv(self._loc(name), 1, GL.GL_TRUE, mat)

    def set_vec3(self, name: str, v: Tuple[float, float, float]) -> None:
        from OpenGL import GL
        GL.glUniform3f(self._loc(name), *v)

    def set_float(self, name: str, v: float) -> None:
        from OpenGL import GL
        GL.glUniform1f(self._loc(name), v)

    def set_int(self, name: str, v: int) -> None:
        from OpenGL import GL
        GL.glUniform1i(self._loc(name), v)

    def delete(self) -> None:
        from OpenGL import GL
        GL.glDeleteProgram(self._id)
        self._id = 0
