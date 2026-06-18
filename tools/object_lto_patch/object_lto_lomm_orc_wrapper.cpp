#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <cstdint>
#include <cstring>
#include <string>
#include <vector>

namespace {

constexpr int SERVEROBJ_VERSION = 1;
constexpr const char *kNewClassName = "LoMMOrcMage";
constexpr const char *kParentClassName = "LizardOrcMage";
constexpr std::uintptr_t kActorRowConstructorRva = 0x0000C0C0;
constexpr std::uintptr_t kLizardOrcMageConstructorRva = 0x00033CF0;
constexpr std::uintptr_t kLizardOrcMageVTableRva = 0x00147A74;
// Runtime constructor IDs are the actor IDs pushed by native object.lto
// constructors. A truly new ID such as 306 can crash level loading because MM9
// appears to have fixed internal actor-definition tables. Row 121 already
// exists in the stock actor tables as Dwarven Soldier and has no shipped DAT
// instances, making it a safer sacrificial table slot than spell-created
// PhantomFighter.
constexpr int kLoMMOrcMageRow = 121;

struct LTVector {
    float x;
    float y;
    float z;
};

struct PropDef {
    char *m_PropName;
    short m_PropType;
    LTVector m_DefaultValueVector;
    float m_DefaultValueFloat;
    char *m_DefaultValueString;
    unsigned long m_PropFlags;
    void *m_pDEditInternal;
    void *m_pInternal;
};

struct ClassDef {
    char *m_ClassName;
    ClassDef *m_ParentClass;
    std::uint32_t m_ClassFlags;
    void *m_ConstructFn;
    void *m_DestructFn;
    void *m_PluginFn;
    short m_nProps;
    PropDef *m_Props;
    long m_ClassObjectSize;
    void *m_pInternal[2];
};

using ObjectDLLSetupFn = ClassDef **(__cdecl *)(int *nDefs, void *pServer, int *version);
using SetInstanceHandleFn = void (__cdecl *)(void *handle);
using GetServerShellVersionFn = int (__cdecl *)();
using GetServerShellFunctionsFn = void (__cdecl *)(void *pCreate, void *pDelete);
using ActorRowConstructorFn = void *(__thiscall *)(void *object, int rowNumber);

HMODULE g_baseModule = nullptr;
ObjectDLLSetupFn g_baseObjectDLLSetup = nullptr;
SetInstanceHandleFn g_baseSetInstanceHandle = nullptr;
GetServerShellVersionFn g_baseGetServerShellVersion = nullptr;
GetServerShellFunctionsFn g_baseGetServerShellFunctions = nullptr;
ClassDef g_lommOrcMageClass = {};
std::vector<ClassDef *> g_classList;
bool g_classInitialized = false;

std::wstring module_path() {
    HMODULE self = nullptr;
    GetModuleHandleExW(
        GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS |
            GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
        reinterpret_cast<LPCWSTR>(&module_path),
        &self);
    wchar_t buffer[MAX_PATH] = {};
    GetModuleFileNameW(self, buffer, MAX_PATH);
    return std::wstring(buffer);
}

std::wstring module_dir() {
    std::wstring path = module_path();
    std::size_t slash = path.find_last_of(L"\\/");
    if (slash == std::wstring::npos) {
        return L".";
    }
    return path.substr(0, slash);
}

void load_base_module() {
    if (g_baseModule) {
        return;
    }

    std::wstring dir = module_dir();
    SetDllDirectoryW(dir.c_str());
    std::wstring basePath = dir + L"\\object_lto_base.lto";
    g_baseModule = LoadLibraryW(basePath.c_str());
    if (!g_baseModule) {
        return;
    }

    g_baseObjectDLLSetup = reinterpret_cast<ObjectDLLSetupFn>(
        GetProcAddress(g_baseModule, "ObjectDLLSetup"));
    g_baseSetInstanceHandle = reinterpret_cast<SetInstanceHandleFn>(
        GetProcAddress(g_baseModule, "SetInstanceHandle"));
    g_baseGetServerShellVersion = reinterpret_cast<GetServerShellVersionFn>(
        GetProcAddress(g_baseModule, "GetServerShellVersion"));
    g_baseGetServerShellFunctions = reinterpret_cast<GetServerShellFunctionsFn>(
        GetProcAddress(g_baseModule, "GetServerShellFunctions"));
}

ClassDef *find_class(ClassDef **classes, int count, const char *name) {
    if (!classes || !name) {
        return nullptr;
    }
    for (int i = 0; i < count; ++i) {
        ClassDef *candidate = classes[i];
        if (candidate && candidate->m_ClassName &&
            std::strcmp(candidate->m_ClassName, name) == 0) {
            return candidate;
        }
    }
    return nullptr;
}

void *__cdecl construct_lomm_orc_mage(void *object) {
    if (!object) {
        return object;
    }
    load_base_module();
    if (!g_baseModule) {
        return object;
    }

    auto base = reinterpret_cast<std::uintptr_t>(g_baseModule);
    auto actorRowConstructor = reinterpret_cast<ActorRowConstructorFn>(
        base + kActorRowConstructorRva);
    actorRowConstructor(object, kLoMMOrcMageRow);

    *reinterpret_cast<void **>(object) = reinterpret_cast<void *>(
        base + kLizardOrcMageVTableRva);
    return object;
}

bool can_use_row_bound_constructor(ClassDef *parent) {
    if (!parent || !g_baseModule) {
        return false;
    }
    auto base = reinterpret_cast<std::uintptr_t>(g_baseModule);
    auto expectedParentConstructor = reinterpret_cast<void *>(
        base + kLizardOrcMageConstructorRva);
    return parent->m_ConstructFn == expectedParentConstructor;
}

ClassDef **append_lomm_orc_class(ClassDef **baseClasses, int baseCount, int *outCount) {
    if (!baseClasses || baseCount < 0) {
        if (outCount) {
            *outCount = baseCount;
        }
        return baseClasses;
    }

    if (find_class(baseClasses, baseCount, kNewClassName)) {
        if (outCount) {
            *outCount = baseCount;
        }
        return baseClasses;
    }

    ClassDef *parent = find_class(baseClasses, baseCount, kParentClassName);
    if (!parent) {
        if (outCount) {
            *outCount = baseCount;
        }
        return baseClasses;
    }

    if (!g_classInitialized) {
        g_lommOrcMageClass.m_ClassName = const_cast<char *>(kNewClassName);
        g_lommOrcMageClass.m_ParentClass = parent;
        g_lommOrcMageClass.m_ClassFlags = 0;
        g_lommOrcMageClass.m_ConstructFn = can_use_row_bound_constructor(parent)
            ? reinterpret_cast<void *>(&construct_lomm_orc_mage)
            : parent->m_ConstructFn;
        g_lommOrcMageClass.m_DestructFn = parent->m_DestructFn;
        g_lommOrcMageClass.m_PluginFn = parent->m_PluginFn;
        g_lommOrcMageClass.m_nProps = 0;
        g_lommOrcMageClass.m_Props = nullptr;
        g_lommOrcMageClass.m_ClassObjectSize = parent->m_ClassObjectSize;
        g_lommOrcMageClass.m_pInternal[0] = nullptr;
        g_lommOrcMageClass.m_pInternal[1] = nullptr;
        g_classInitialized = true;
    }

    g_classList.assign(baseClasses, baseClasses + baseCount);
    g_classList.push_back(&g_lommOrcMageClass);
    if (outCount) {
        *outCount = static_cast<int>(g_classList.size());
    }
    return g_classList.data();
}

}  // namespace

extern "C" __declspec(dllexport)
ClassDef **__cdecl ObjectDLLSetup(int *nDefs, void *pServer, int *version) {
    load_base_module();
    if (!g_baseObjectDLLSetup) {
        if (nDefs) {
            *nDefs = 0;
        }
        if (version) {
            *version = SERVEROBJ_VERSION;
        }
        return nullptr;
    }

    int baseCount = 0;
    int baseVersion = 0;
    ClassDef **baseClasses = g_baseObjectDLLSetup(&baseCount, pServer, &baseVersion);
    if (version) {
        *version = baseVersion;
    }
    if (baseVersion != SERVEROBJ_VERSION || !baseClasses || baseCount < 0) {
        if (nDefs) {
            *nDefs = baseCount;
        }
        return baseClasses;
    }
    return append_lomm_orc_class(baseClasses, baseCount, nDefs);
}

extern "C" __declspec(dllexport)
void __cdecl SetInstanceHandle(void *handle) {
    load_base_module();
    if (g_baseSetInstanceHandle) {
        g_baseSetInstanceHandle(handle);
    }
}

extern "C" __declspec(dllexport)
int __cdecl GetServerShellVersion() {
    load_base_module();
    if (g_baseGetServerShellVersion) {
        return g_baseGetServerShellVersion();
    }
    return 3;
}

extern "C" __declspec(dllexport)
void __cdecl GetServerShellFunctions(void *pCreate, void *pDelete) {
    load_base_module();
    if (g_baseGetServerShellFunctions) {
        g_baseGetServerShellFunctions(pCreate, pDelete);
    }
}

BOOL WINAPI DllMain(HINSTANCE, DWORD, LPVOID) {
    return TRUE;
}
