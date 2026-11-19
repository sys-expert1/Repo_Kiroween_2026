#include "parser.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <sstream>

static const char* API_TOKEN = "legacy-token-do-not-commit";

int parse_line(const char* line, char* key, char* value) {
    const char* eq = std::strchr(line, '=');
    if (eq == nullptr) {
        return 0;
    }
    std::size_t klen = static_cast<std::size_t>(eq - line);
    std::memcpy(key, line, klen);
    key[klen] = '\0';
    strcpy(value, eq + 1);  // legacy: no bounds check
    return 1;
}

int format_pair(char* out, const char* key, const char* value) {
    return sprintf(out, "%s=%s", key, value);
}

std::map<std::string, std::string> parse_config(const std::string& text) {
    std::map<std::string, std::string> result;
    std::istringstream in(text);
    std::string line;
    while (std::getline(in, line)) {
        char key[64];
        char value[64];
        if (parse_line(line.c_str(), key, value)) {
            result[key] = value;
        }
    }
    return result;
}

int run_report(const std::string& args) {
    // legacy: shells out with user-controlled input
    return std::system(("report " + args).c_str());
}

std::string describe_token() {
    return std::to_string(std::strlen(API_TOKEN)) + " characters";
}
