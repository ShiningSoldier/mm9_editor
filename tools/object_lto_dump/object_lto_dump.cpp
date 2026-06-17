// Dumps LithTech object.lto ClassDef metadata as JSON.
//
// This intentionally mirrors the small part of DEdit's class loading path that
// the catalog builder needs: load object.lto, call ObjectDLLSetup, and flatten
// inherited properties with child definitions replacing parent definitions.

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <algorithm>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iomanip>
#include <iostream>
#include <map>
#include <sstream>
#include <string>
#include <utility>
#include <vector>

namespace {

constexpr int SERVEROBJ_VERSION = 1;

enum PropertyType {
    PT_STRING = 0,
    PT_VECTOR = 1,
    PT_COLOR = 2,
    PT_REAL = 3,
    PT_FLAGS = 4,
    PT_BOOL = 5,
    PT_LONGINT = 6,
    PT_ROTATION = 7,
    NUM_PROPERTYTYPES = 8,
};

enum PropertyFlags : std::uint32_t {
    PF_HIDDEN = 1u << 0,
    PF_RADIUS = 1u << 1,
    PF_DIMS = 1u << 2,
    PF_FIELDOFVIEW = 1u << 3,
    PF_LOCALDIMS = 1u << 4,
    PF_GROUPOWNER = 1u << 5,
    PF_FOVRADIUS = 1u << 12,
    PF_OBJECTLINK = 1u << 13,
    PF_FILENAME = 1u << 14,
    PF_BEZIERPREVTANGENT = 1u << 15,
    PF_BEZIERNEXTTANGENT = 1u << 16,
    PF_STATICLIST = 1u << 17,
    PF_DYNAMICLIST = 1u << 18,
    PF_COMPOSITETYPE = 1u << 19,
    PF_DISTANCE = 1u << 20,
    PF_MODEL = 1u << 21,
    PF_ORTHOFRUSTUM = 1u << 22,
    PF_NOTIFYCHANGE = 1u << 23,
    PF_EVENT = 1u << 24,
    PF_TEXTUREEFFECT = 1u << 25,
};

constexpr int FIRST_GROUP_BIT = 6;
constexpr int NUM_GROUP_BITS = 6;
constexpr std::uint32_t PF_GROUPMASK =
    ((1u << NUM_GROUP_BITS) - 1u) << FIRST_GROUP_BIT;

enum ClassFlags : std::uint32_t {
    CF_HIDDEN = 1u << 0,
    CF_NORUNTIME = 1u << 1,
    CF_STATIC = 1u << 2,
    CF_ALWAYSLOAD = 1u << 3,
    CF_WORLDMODEL = 1u << 4,
    CF_CLASSONLY = 1u << 5,
};

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

std::string narrow(const std::wstring &value) {
    if (value.empty()) {
        return std::string();
    }
    int size = WideCharToMultiByte(CP_UTF8, 0, value.c_str(), -1, nullptr, 0, nullptr, nullptr);
    if (size <= 0) {
        return std::string();
    }
    std::string result(static_cast<std::size_t>(size - 1), '\0');
    WideCharToMultiByte(CP_UTF8, 0, value.c_str(), -1, &result[0], size, nullptr, nullptr);
    return result;
}

std::wstring dirname(const std::wstring &path) {
    std::size_t slash = path.find_last_of(L"\\/");
    if (slash == std::wstring::npos) {
        return L".";
    }
    return path.substr(0, slash);
}

std::string last_error_message(DWORD code) {
    LPWSTR buffer = nullptr;
    DWORD chars = FormatMessageW(
        FORMAT_MESSAGE_ALLOCATE_BUFFER | FORMAT_MESSAGE_FROM_SYSTEM |
            FORMAT_MESSAGE_IGNORE_INSERTS,
        nullptr, code, MAKELANGID(LANG_NEUTRAL, SUBLANG_DEFAULT),
        reinterpret_cast<LPWSTR>(&buffer), 0, nullptr);
    if (chars == 0 || buffer == nullptr) {
        std::ostringstream out;
        out << "Windows error " << code;
        return out.str();
    }
    std::wstring wide(buffer, chars);
    LocalFree(buffer);
    while (!wide.empty() && (wide.back() == L'\n' || wide.back() == L'\r')) {
        wide.pop_back();
    }
    return narrow(wide);
}

std::string safe_string(const char *value) {
    return value ? std::string(value) : std::string();
}

void write_json_string(std::ostream &out, const std::string &value) {
    out << '"';
    for (unsigned char ch : value) {
        switch (ch) {
            case '\\': out << "\\\\"; break;
            case '"': out << "\\\""; break;
            case '\b': out << "\\b"; break;
            case '\f': out << "\\f"; break;
            case '\n': out << "\\n"; break;
            case '\r': out << "\\r"; break;
            case '\t': out << "\\t"; break;
            default:
                if (ch < 0x20) {
                    out << "\\u" << std::hex << std::setw(4) << std::setfill('0')
                        << static_cast<int>(ch) << std::dec << std::setfill(' ');
                } else {
                    out << ch;
                }
                break;
        }
    }
    out << '"';
}

std::string prop_type_name(int type) {
    switch (type) {
        case PT_STRING: return "string";
        case PT_VECTOR: return "vector";
        case PT_COLOR: return "color";
        case PT_REAL: return "real";
        case PT_FLAGS: return "flags";
        case PT_BOOL: return "bool";
        case PT_LONGINT: return "longint";
        case PT_ROTATION: return "rotation";
        default: return "unknown";
    }
}

std::vector<std::string> class_flag_names(std::uint32_t flags) {
    std::vector<std::pair<std::uint32_t, const char *>> known = {
        {CF_HIDDEN, "CF_HIDDEN"},
        {CF_NORUNTIME, "CF_NORUNTIME"},
        {CF_STATIC, "CF_STATIC"},
        {CF_ALWAYSLOAD, "CF_ALWAYSLOAD"},
        {CF_WORLDMODEL, "CF_WORLDMODEL"},
        {CF_CLASSONLY, "CF_CLASSONLY"},
    };
    std::vector<std::string> names;
    for (const auto &entry : known) {
        if (flags & entry.first) {
            names.emplace_back(entry.second);
        }
    }
    return names;
}

std::vector<std::string> prop_flag_names(std::uint32_t flags) {
    std::vector<std::pair<std::uint32_t, const char *>> known = {
        {PF_HIDDEN, "PF_HIDDEN"},
        {PF_RADIUS, "PF_RADIUS"},
        {PF_DIMS, "PF_DIMS"},
        {PF_FIELDOFVIEW, "PF_FIELDOFVIEW"},
        {PF_LOCALDIMS, "PF_LOCALDIMS"},
        {PF_GROUPOWNER, "PF_GROUPOWNER"},
        {PF_FOVRADIUS, "PF_FOVRADIUS"},
        {PF_OBJECTLINK, "PF_OBJECTLINK"},
        {PF_FILENAME, "PF_FILENAME"},
        {PF_BEZIERPREVTANGENT, "PF_BEZIERPREVTANGENT"},
        {PF_BEZIERNEXTTANGENT, "PF_BEZIERNEXTTANGENT"},
        {PF_STATICLIST, "PF_STATICLIST"},
        {PF_DYNAMICLIST, "PF_DYNAMICLIST"},
        {PF_COMPOSITETYPE, "PF_COMPOSITETYPE"},
        {PF_DISTANCE, "PF_DISTANCE"},
        {PF_MODEL, "PF_MODEL"},
        {PF_ORTHOFRUSTUM, "PF_ORTHOFRUSTUM"},
        {PF_NOTIFYCHANGE, "PF_NOTIFYCHANGE"},
        {PF_EVENT, "PF_EVENT"},
        {PF_TEXTUREEFFECT, "PF_TEXTUREEFFECT"},
    };
    std::vector<std::string> names;
    for (const auto &entry : known) {
        if (flags & entry.first) {
            names.emplace_back(entry.second);
        }
    }
    return names;
}

void write_string_array(std::ostream &out, const std::vector<std::string> &values) {
    out << '[';
    for (std::size_t i = 0; i < values.size(); ++i) {
        if (i != 0) {
            out << ',';
        }
        write_json_string(out, values[i]);
    }
    out << ']';
}

void write_vector(std::ostream &out, const LTVector &value) {
    out << '[' << value.x << ',' << value.y << ',' << value.z << ']';
}

void write_default_value(std::ostream &out, const PropDef &prop) {
    switch (prop.m_PropType) {
        case PT_STRING:
            write_json_string(out, safe_string(prop.m_DefaultValueString));
            break;
        case PT_VECTOR:
        case PT_COLOR:
        case PT_ROTATION:
            write_vector(out, prop.m_DefaultValueVector);
            break;
        case PT_REAL:
            out << prop.m_DefaultValueFloat;
            break;
        case PT_FLAGS:
        case PT_LONGINT:
            out << static_cast<long>(prop.m_DefaultValueFloat);
            break;
        case PT_BOOL:
            out << (prop.m_DefaultValueFloat != 0.0f ? "true" : "false");
            break;
        default:
            out << "null";
            break;
    }
}

std::vector<ClassDef *> class_chain(ClassDef *class_def) {
    std::vector<ClassDef *> chain;
    for (ClassDef *current = class_def; current != nullptr; current = current->m_ParentClass) {
        chain.push_back(current);
    }
    std::reverse(chain.begin(), chain.end());
    return chain;
}

std::vector<std::pair<PropDef *, ClassDef *>> flattened_props(ClassDef *class_def) {
    std::vector<std::pair<PropDef *, ClassDef *>> props;
    for (ClassDef *current : class_chain(class_def)) {
        for (int i = 0; i < current->m_nProps; ++i) {
            PropDef *prop = &current->m_Props[i];
            const std::string name = safe_string(prop->m_PropName);
            auto found = std::find_if(
                props.begin(), props.end(),
                [&](const std::pair<PropDef *, ClassDef *> &existing) {
                    return safe_string(existing.first->m_PropName) == name;
                });
            if (found != props.end()) {
                props.erase(found);
            }
            props.emplace_back(prop, current);
        }
    }

    std::vector<std::pair<PropDef *, ClassDef *>> visible;
    std::vector<std::pair<PropDef *, ClassDef *>> hidden;
    for (const auto &entry : props) {
        std::uint32_t flags = static_cast<std::uint32_t>(entry.first->m_PropFlags);
        if ((flags & PF_HIDDEN) || ((flags & PF_GROUPMASK) && !(flags & PF_GROUPOWNER))) {
            hidden.push_back(entry);
        } else {
            visible.push_back(entry);
        }
    }
    visible.insert(visible.end(), hidden.begin(), hidden.end());
    return visible;
}

void write_prop(std::ostream &out, const PropDef &prop, const ClassDef *source_class) {
    std::uint32_t flags = static_cast<std::uint32_t>(prop.m_PropFlags);
    bool hidden_or_group_child =
        (flags & PF_HIDDEN) || ((flags & PF_GROUPMASK) && !(flags & PF_GROUPOWNER));

    out << '{';
    out << "\"name\":";
    write_json_string(out, safe_string(prop.m_PropName));
    out << ",\"source_class\":";
    write_json_string(out, source_class ? safe_string(source_class->m_ClassName) : "");
    out << ",\"type_id\":" << prop.m_PropType;
    out << ",\"type\":";
    write_json_string(out, prop_type_name(prop.m_PropType));
    out << ",\"flags\":" << flags;
    out << ",\"flag_names\":";
    write_string_array(out, prop_flag_names(flags));
    out << ",\"group\":" << ((flags & PF_GROUPMASK) >> FIRST_GROUP_BIT);
    out << ",\"hidden_in_dedit\":" << (hidden_or_group_child ? "true" : "false");
    out << ",\"default_value\":";
    write_default_value(out, prop);
    out << ",\"default_raw\":{";
    out << "\"vector\":";
    write_vector(out, prop.m_DefaultValueVector);
    out << ",\"float\":" << prop.m_DefaultValueFloat;
    out << ",\"string\":";
    if (prop.m_DefaultValueString) {
        write_json_string(out, prop.m_DefaultValueString);
    } else {
        out << "null";
    }
    out << "}}";
}

void write_class(std::ostream &out, ClassDef *class_def) {
    std::uint32_t flags = class_def->m_ClassFlags;
    auto chain = class_chain(class_def);
    auto props = flattened_props(class_def);

    out << '{';
    out << "\"name\":";
    write_json_string(out, safe_string(class_def->m_ClassName));
    out << ",\"parent\":";
    if (class_def->m_ParentClass) {
        write_json_string(out, safe_string(class_def->m_ParentClass->m_ClassName));
    } else {
        out << "null";
    }
    out << ",\"hierarchy\":[";
    for (std::size_t i = 0; i < chain.size(); ++i) {
        if (i != 0) {
            out << ',';
        }
        write_json_string(out, safe_string(chain[i]->m_ClassName));
    }
    out << ']';
    out << ",\"flags\":" << flags;
    out << ",\"flag_names\":";
    write_string_array(out, class_flag_names(flags));
    out << ",\"hidden_in_dedit\":" << ((flags & CF_HIDDEN) ? "true" : "false");
    out << ",\"runtime_loadable\":" << ((flags & CF_NORUNTIME) ? "false" : "true");
    out << ",\"class_object_size\":" << class_def->m_ClassObjectSize;

    out << ",\"declared_properties\":[";
    for (int i = 0; i < class_def->m_nProps; ++i) {
        if (i != 0) {
            out << ',';
        }
        write_prop(out, class_def->m_Props[i], class_def);
    }
    out << ']';

    out << ",\"properties\":[";
    for (std::size_t i = 0; i < props.size(); ++i) {
        if (i != 0) {
            out << ',';
        }
        write_prop(out, *props[i].first, props[i].second);
    }
    out << "]}";
}

void write_dump(
    std::ostream &out,
    const std::string &object_lto_path,
    int version,
    ClassDef **classes,
    int class_count) {
    out << std::setprecision(9);
    out << '{';
    out << "\"schema\":\"mm9_editor.object_lto_dump.v1\"";
    out << ",\"object_lto_path\":";
    write_json_string(out, object_lto_path);
    out << ",\"server_object_version\":" << version;
    out << ",\"class_count\":" << class_count;
    out << ",\"classes\":[";
    for (int i = 0; i < class_count; ++i) {
        if (i != 0) {
            out << ',';
        }
        write_class(out, classes[i]);
    }
    out << "]}";
}

int dump_object_lto(const std::wstring &object_lto_path, std::ostream &out) {
    std::wstring module_dir = dirname(object_lto_path);
    SetDllDirectoryW(module_dir.c_str());
    SetCurrentDirectoryW(module_dir.c_str());

    HMODULE module = LoadLibraryW(object_lto_path.c_str());
    if (!module) {
        DWORD err = GetLastError();
        std::cerr << "ERROR: failed to load object.lto: " << last_error_message(err) << "\n";
        return 2;
    }

    auto setup = reinterpret_cast<ObjectDLLSetupFn>(GetProcAddress(module, "ObjectDLLSetup"));
    if (!setup) {
        std::cerr << "ERROR: ObjectDLLSetup export was not found\n";
        FreeLibrary(module);
        return 3;
    }

    int class_count = 0;
    int version = 0;
    ClassDef **classes = setup(&class_count, nullptr, &version);
    if (version != SERVEROBJ_VERSION) {
        std::cerr << "ERROR: server object version mismatch. expected "
                  << SERVEROBJ_VERSION << ", got " << version << "\n";
        FreeLibrary(module);
        return 4;
    }
    if (!classes || class_count < 0) {
        std::cerr << "ERROR: ObjectDLLSetup returned an invalid class list\n";
        FreeLibrary(module);
        return 5;
    }

    write_dump(out, narrow(object_lto_path), version, classes, class_count);
    out << "\n";
    FreeLibrary(module);
    return 0;
}

}  // namespace

int wmain(int argc, wchar_t **argv) {
    if (argc != 2) {
        std::cerr << "usage: object_lto_dump.exe <path-to-object.lto>\n";
        return 1;
    }
    return dump_object_lto(argv[1], std::cout);
}
