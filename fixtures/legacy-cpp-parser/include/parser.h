#ifndef PARSER_H
#define PARSER_H

#include <map>
#include <string>

// Splits "key=value" into the two caller-provided buffers. Returns 1 on success.
// Legacy contract: the caller must provide buffers that are large enough.
int parse_line(const char* line, char* key, char* value);

// Writes "key=value" into `out`. Returns the number of characters written.
int format_pair(char* out, const char* key, const char* value);

std::map<std::string, std::string> parse_config(const std::string& text);

// Runs the external `report` tool with the given arguments.
int run_report(const std::string& args);

// Describes the configured token (its length only).
std::string describe_token();

#endif
