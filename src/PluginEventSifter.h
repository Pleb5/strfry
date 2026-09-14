#pragma once

#include <string.h>
#include <errno.h>
#include <spawn.h>
#include <unistd.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <sys/types.h>
#include <sys/stat.h>
#include <signal.h>

#include <memory>

#if defined(__APPLE__) || defined(__FreeBSD__) || defined(__OpenBSD__) || defined(__NetBSD__) || defined(__DragonFly__)
#define st_mtim st_mtimespec
#endif

#include "hoytech/stream.h"

#include "golpe.h"

#include "events.h"

#if defined(__FreeBSD__) || defined(__OpenBSD__) || defined(__NetBSD__) || defined(__DragonFly__)
extern char **environ;
#elif defined(__APPLE__)
#include <crt_externs.h>
#define environ (*_NSGetEnviron())
#endif



enum class PluginEventSifterResult {
    Accept,
    Reject,
    ShadowReject,
};


struct PluginEventSifter {
    struct RunningPlugin {
        pid_t pid;
        hoytech::StreamReader streamReader;
        hoytech::StreamWriter streamWriter;
        std::string currPluginCmd;
        struct timespec lastModTime;
        bool privateProcessGroup = false;

        RunningPlugin(pid_t pid, int rfd, int wfd, std::string currPluginCmd) : pid(pid), streamReader(rfd), streamWriter(wfd), currPluginCmd(currPluginCmd) {
            streamReader.setMaxRecordSize(8192);

            if (currPluginCmd.find(' ') == std::string::npos) {
                struct stat statbuf;
                if (stat(currPluginCmd.c_str(), &statbuf)) throw herr("couldn't stat plugin: ", currPluginCmd);
                lastModTime = statbuf.st_mtim;
            }
        }

        ~RunningPlugin() {
            // A stopped or uncooperative private policy must not deadlock its
            // writer during restart. Kill its process group, including scans.
            ::kill(privateProcessGroup ? -pid : pid, privateProcessGroup ? SIGKILL : SIGTERM);
            ::waitpid(pid, nullptr, 0);
        }
    };

    std::unique_ptr<RunningPlugin> running; 
    bool privateMode = false;
    bool policyRelevant = false;
    std::function<void()> onStart, onStop;

    void stop() {
        if (onStop) onStop();
        running.reset();
    }

    void prepare(const std::string &pluginCmd) {
        if (running) {
            int status;
            if (privateMode && waitpid(running->pid, &status, WNOHANG) == running->pid) stop();
        }
        if (running) {
            if (pluginCmd != running->currPluginCmd) stop();
            else if (pluginCmd.find(' ') == std::string::npos) {
                struct stat st;
                if (stat(pluginCmd.c_str(), &st)) throw herr("couldn't stat plugin");
                if (st.st_mtim.tv_sec != running->lastModTime.tv_sec || st.st_mtim.tv_nsec != running->lastModTime.tv_nsec) stop();
            }
        }
        if (!running) {
            setupPlugin(pluginCmd);
            if (onStart) onStart();
        }
    }

    void sendControl(const tao::json::value &message) {
        if (!running) throw herr("policy process unavailable");
        running->streamWriter.write(tao::json::to_string(message) + "\n", 1000);
    }

    PluginEventSifterResult acceptEvent(const std::string &pluginCmd, const tao::json::value &evJson, EventSourceType sourceType, std::string_view sourceInfo, const Bytes32 &authed, std::string &okMsg) {
        policyRelevant = false;
        if (pluginCmd.size() == 0) {
            running.reset();
            return PluginEventSifterResult::Accept;
        }

        try {
            prepare(pluginCmd);

            auto request = tao::json::value({
                { "type", "new" },
                { "event", evJson },
                { "receivedAt", ::time(nullptr) },
                { "sourceType", eventSourceTypeToStr(sourceType) },
                { "sourceInfo", sourceType == EventSourceType::IP4 || sourceType == EventSourceType::IP6 ? renderIP(sourceInfo) : sourceInfo },
            });

            if (!authed.isNull()) request["authed"] = to_hex(authed.sv());

            std::string output = tao::json::to_string(request);
            output += "\n";

            try {
                running->streamWriter.write(output, cfg().relay__writePolicy__timeoutSeconds * 1'000);
            } catch (std::exception &e) {
                throw herr("Failed to write event: ", e.what(), ". Request was: ", output);
            }

            tao::json::value response;

            while (1) {
                std::string line;

                try {
                    line = running->streamReader.read(cfg().relay__writePolicy__timeoutSeconds * 1'000);
                } catch (std::exception &e) {
                    throw herr("Failed to read response: ", e.what(), ". Request was: ", output);
                }

                try {
                    response = tao::json::from_string(line);
                } catch (std::exception &e) {
                    if (privateMode) throw herr("invalid private policy response");
                    LW << "Got unparseable line from write policy plugin: " << line;
                    continue;
                }

                if (response.at("id").get_string() != request.at("event").at("id").get_string()) throw herr("id mismatch");

                break;
            }

            okMsg = response.optional<std::string>("msg").value_or("");

            auto action = response.at("action").get_string();
            if (privateMode && action == "accept") policyRelevant = response.optional<bool>("policyRelevant").value_or(false);
            if (action == "accept") return PluginEventSifterResult::Accept;
            else if (action == "reject") return PluginEventSifterResult::Reject;
            else if (action == "shadowReject") return PluginEventSifterResult::ShadowReject;
            else throw herr("unknown action: ", action);
        } catch (std::exception &e) {
            LE << "Plugin error: " << (privateMode ? "private policy unavailable" : e.what());
            stop();
            okMsg = "error: internal error";
            return PluginEventSifterResult::Reject;
        }
    }


    struct Pipe : NonCopyable {
        int fds[2] = { -1, -1 };

        Pipe() {
            if (::pipe(fds)) throw herr("pipe failed: ", strerror(errno));
        }

        Pipe(int fd0, int fd1) {
            fds[0] = fd0;
            fds[1] = fd1;
        }

        ~Pipe() {
            if (fds[0] != -1) ::close(fds[0]);
            if (fds[1] != -1) ::close(fds[1]);
        }

        int extractFd(int offset) {
            int fd = fds[offset];
            fds[offset] = -1;
            return fd;
        }
    };

  private:
    void setupPlugin(const std::string &pluginCmd) {
        LI << "Setting up write policy plugin: " << pluginCmd;

        Pipe outPipe;
        Pipe inPipe;

        pid_t pid;
        const char * const argv[] = { "/bin/sh", "-c", pluginCmd.c_str(), nullptr, };

        // FIXME: currently leaks. Should call posix_spawn_file_actions_destroy in a RAII wrapper
        posix_spawn_file_actions_t file_actions;

        if (
            posix_spawn_file_actions_init(&file_actions) ||
            posix_spawn_file_actions_adddup2(&file_actions, outPipe.fds[0], 0) ||
            posix_spawn_file_actions_adddup2(&file_actions, inPipe.fds[1], 1) ||
            posix_spawn_file_actions_addclose(&file_actions, outPipe.fds[0]) ||
            posix_spawn_file_actions_addclose(&file_actions, outPipe.fds[1]) ||
            posix_spawn_file_actions_addclose(&file_actions, inPipe.fds[0]) ||
            posix_spawn_file_actions_addclose(&file_actions, inPipe.fds[1])
        ) throw herr("posix_span_file_actions failed: ", strerror(errno));

        posix_spawnattr_t attributes;
        posix_spawnattr_init(&attributes);
        if (privateMode) {
            posix_spawnattr_setflags(&attributes, POSIX_SPAWN_SETPGROUP);
            posix_spawnattr_setpgroup(&attributes, 0);
        }
        auto ret = posix_spawnp(&pid, "sh", &file_actions, &attributes, (char* const*)(&argv[0]), environ);
        posix_spawnattr_destroy(&attributes);
        posix_spawn_file_actions_destroy(&file_actions);
        if (ret) throw herr("posix_spawn failed to invoke '", pluginCmd, "': ", strerror(errno));

        running = make_unique<RunningPlugin>(pid, inPipe.extractFd(0), outPipe.extractFd(1), pluginCmd);
        running->privateProcessGroup = privateMode;
    }
};
