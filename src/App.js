import React, { useState, useRef } from "react";
import { v4 as uuidv4 } from "uuid"; // npm install uuid

function App() {
  const [request, setRequest] = useState("");
  const [infoChips, setInfoChips] = useState([]);
  const [chipInput, setChipInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState("");
  const [percent, setPercent] = useState(0);
  const [results, setResults] = useState({ offers: [], useful_sites: [] });
  const [error, setError] = useState(null);

  // CHANGE: backendUrl as state!
  const [backendUrl, setBackendUrl] = useState("http://localhost:8000");

  const chipInputRef = useRef();
  const controllerRef = useRef(null); // For aborting fetch
  const streamIdRef = useRef(null);

  function getSearchUrl() {
    return backendUrl.replace(/\/+$/, "") + "/search";
  }
  function getStopUrl() {
    return backendUrl.replace(/\/+$/, "") + "/stop";
  }

  function handleChipInput(e) {
    if (
      e.type === "blur" ||
      (e.type === "keydown" &&
        (e.key === "Enter" || e.key === "," || e.key === "Tab"))
    ) {
      e.preventDefault();
      const trimmed = chipInput.trim().replace(/,$/, "");
      if (trimmed && !infoChips.includes(trimmed.toLowerCase())) {
        setInfoChips([...infoChips, trimmed]);
      }
      setChipInput("");
    }
  }

  function removeChip(idx) {
    setInfoChips(infoChips.filter((_, i) => i !== idx));
    setTimeout(() => chipInputRef.current && chipInputRef.current.focus(), 100);
  }

  // ---- STOP logic ----
  const handleStop = async () => {
    if (controllerRef.current) controllerRef.current.abort();
    setLoading(false);
    setStatus("Stopped.");
    setPercent(100);
    if (streamIdRef.current) {
      try {
        await fetch(getStopUrl(), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ stream_id: streamIdRef.current }),
        });
      } catch (e) {}
    }
  };

  // Main submit with streaming progress support
  const handleSubmit = async (e, manualUrl = null) => {
    e.preventDefault();
    if (loading || status.startsWith("Stopped")) {
      setStatus("");
      return;
    }

    setLoading(true);
    setError(null);
    setResults({ offers: [], useful_sites: [] });
    setStatus("");
    setPercent(0);

    const thisStreamId = uuidv4();
    streamIdRef.current = thisStreamId;
    const controller = new AbortController();
    controllerRef.current = controller;

    let urlToUse = manualUrl || getSearchUrl();

    try {
      const res = await fetch(urlToUse, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal: controller.signal,
        body: JSON.stringify({
          request_string: request,
          info_type: infoChips,
          stream_id: thisStreamId,
        }),
      });
      if (!res.body) throw new Error("No response body from backend.");

      const reader = res.body.getReader();
      let buffer = "";
      let done = false;
      let offersSoFar = [];
      let usefulSitesSoFar = [];
      while (!done) {
        const { value, done: streamDone } = await reader.read();
        done = streamDone;
        if (value) {
          buffer += new TextDecoder().decode(value, { stream: true });
          let lines = buffer.split("\n");
          buffer = lines.pop();
          for (const line of lines) {
            if (!line.trim()) continue;
            let chunk;
            try {
              chunk = JSON.parse(line);
            } catch (e) {
              continue;
            }
            if (chunk.status) setStatus(chunk.status);
            if (typeof chunk.percent === "number") setPercent(chunk.percent);
            if (chunk.error) setError(chunk.error);

            if (chunk.offer) {
              const url = chunk.offer.url;
              if (!offersSoFar.some((o) => o.url === url)) {
                offersSoFar = [...offersSoFar, chunk.offer];
              }
              if (url && !usefulSitesSoFar.includes(url)) {
                usefulSitesSoFar.push(url);
              }
              setResults({
                offers: offersSoFar,
                useful_sites: usefulSitesSoFar,
              });
            }

            if ((chunk.offers || chunk.useful_sites) && chunk.percent === 100) {
              offersSoFar =
                chunk.offers !== undefined ? chunk.offers : offersSoFar;
              usefulSitesSoFar =
                chunk.useful_sites !== undefined
                  ? chunk.useful_sites
                  : usefulSitesSoFar;
              setResults({
                offers: offersSoFar,
                useful_sites: usefulSitesSoFar,
              });
              setLoading(false);
              setStatus("Done!");
              setPercent(100);
            }
            if (chunk.stopped) {
              setLoading(false);
              setStatus("Stopped.");
              setPercent(100);
            }
          }
        }
      }
      setLoading(false);
      controllerRef.current = null;
      streamIdRef.current = null;
    } catch (err) {
      // HANDLE SERVER UNREACHABLE
      if (
        err.message.includes("Failed to fetch") ||
        err.message.includes("No response body")
      ) {
        let promptUrl = window.prompt(
          "Could not reach the backend server.\nPlease enter a new backend URL (including /search):",
          backendUrl
        );
        if (promptUrl && promptUrl.trim()) {
          setBackendUrl(promptUrl.trim());
          setLoading(false);
          setTimeout(() => {
            handleSubmit(
              { preventDefault: () => {} }, // fake event
              promptUrl.trim()
            );
          }, 100);
        } else {
          setError(
            "Backend server not reachable. Please reload the page and try again."
          );
          setLoading(false);
        }
      } else if (err.name !== "AbortError") {
        setError(err.message || "Unknown error");
        setStatus("Error");
        setLoading(false);
      }
      setPercent(0);
      controllerRef.current = null;
      streamIdRef.current = null;
    }
  };

  const [advancedOpen, setAdvancedOpen] = useState(false);

  function handleAdvancedBlur(e) {
    // Collapse if click is outside input or button
    setTimeout(() => setAdvancedOpen(false), 120);
  }

  function handleBackendUrlChange(e) {
    setBackendUrl(e.target.value);
  }

  // UI as before, but button switches
  return (
    <div
      style={{
        minHeight: "100vh",
        background: "linear-gradient(120deg, #e9efff 0%, #f8fcff 100%)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        fontFamily: "'Inter', 'Segoe UI', sans-serif",
      }}
    >
      <div style={{ position: "absolute", top: 18, right: 18, zIndex: 50 }}>
        <div
          style={{
            background: "#f2f5ff",
            border: "1px solid #c6d5ef",
            borderRadius: "0.75em",
            boxShadow: "0 1px 6px rgba(20,40,90,0.04)",
            padding: advancedOpen ? "13px 18px 16px 18px" : "8px 16px",
            minWidth: 0,
            minHeight: 0,
            transition: "all 0.16s",
            cursor: "pointer",
            fontSize: 15,
            fontWeight: 500,
            color: "#23406a",
            userSelect: "none",
          }}
          onClick={() => setAdvancedOpen((v) => !v)}
        >
          {advancedOpen ? (
            <div style={{ minWidth: 260 }}>
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  marginBottom: 7,
                }}
              >
                <div style={{ fontWeight: 600, color: "#29419f" }}>
                  Backend Server URL
                </div>
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    setAdvancedOpen(false);
                  }}
                  style={{
                    background: "#eee",
                    color: "#3754ad",
                    fontWeight: 600,
                    border: "none",
                    borderRadius: "0.55em",
                    padding: "5px 13px",
                    fontSize: 14,
                    cursor: "pointer",
                    marginTop: 0, // (override previous marginTop)
                  }}
                >
                  Close
                </button>
              </div>

              <input
                style={{
                  width: 270,
                  border: "1px solid #b6c7e3",
                  borderRadius: "0.55em",
                  fontSize: 14,
                  padding: "7px 10px",
                  background: "#fff",
                  color: "#183c7a",
                }}
                value={backendUrl}
                onChange={handleBackendUrlChange}
                // Do NOT close when clicking the input!
                onClick={(e) => e.stopPropagation()}
              />
              {/* <div style={{ fontSize: 13, marginTop: 5, color: "#697ba8" }}> */}
              {/* Ex: http://localhost:8000/search <br /> */}
              {/* (Advanced users only) */}
              {/* </div> */}
            </div>
          ) : (
            <>
              {/* <span style={{ marginRight: 7, fontWeight: 600 }}>Advanced</span> */}
              <span style={{ fontSize: 13, color: "#6b82b8" }}>
                Server:{" "}
                {backendUrl.length > 30
                  ? backendUrl.slice(0, 30) + "..."
                  : backendUrl}
              </span>
            </>
          )}
        </div>
      </div>

      <div
        style={{
          background: "#fff",
          padding: "2.5rem 2rem",
          borderRadius: "1.25rem",
          boxShadow: "0 8px 32px rgba(38,57,120,.11)",
          maxWidth: 540,
          width: "100%",
          margin: "2rem auto",
        }}
      >
        <h2
          style={{
            textAlign: "center",
            fontWeight: 700,
            fontSize: 28,
            color: "#17347a",
            margin: "0 0 1.5rem 0",
            letterSpacing: "0.02em",
          }}
        >
          SpecFetch
        </h2>

        <form onSubmit={handleSubmit} style={{ marginBottom: 28 }}>
          <textarea
            style={{
              width: "100%",
              minHeight: 80,
              border: "1px solid #c6d5ef",
              borderRadius: "0.7rem",
              fontSize: 16,
              padding: "0.75rem 1rem",
              marginBottom: 10,
              resize: "vertical",
              boxSizing: "border-box",
              outline: "none",
              background: "#f6f9fe",
            }}
            value={request}
            onChange={(e) => setRequest(e.target.value)}
            placeholder="Describe the part request (e.g. '20 pcs of P613842 RECEPTACLE PER BELL SPEC')"
            disabled={loading}
            autoFocus
          />

          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              flexWrap: "wrap",
              marginBottom: 16,
              minHeight: 46,
            }}
          >
            <label
              htmlFor="chip-input"
              style={{
                fontSize: 15,
                color: "#3e5376",
                fontWeight: 500,
                marginRight: 8,
                whiteSpace: "nowrap",
              }}
            >
              Info to fetch:
            </label>
            <div
              style={{
                display: "flex",
                alignItems: "center",
                flexWrap: "wrap",
                background: "#f6f9fe",
                border: "1px solid #c6d5ef",
                borderRadius: "0.7rem",
                padding: "5px 10px",
                minHeight: 36,
                minWidth: 0,
                flex: "1 1 auto",
                maxWidth: 360,
              }}
              onClick={() =>
                chipInputRef.current && chipInputRef.current.focus()
              }
            >
              {infoChips.map((chip, idx) => (
                <span
                  key={idx}
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    background: "#e3ecfb",
                    color: "#264fa5",
                    borderRadius: "2em",
                    padding: "0.27em 0.85em 0.27em 0.8em",
                    margin: "2px 3px",
                    fontSize: 14,
                    fontWeight: 500,
                  }}
                >
                  {chip}
                  <button
                    type="button"
                    onClick={() => removeChip(idx)}
                    style={{
                      marginLeft: 5,
                      background: "transparent",
                      border: "none",
                      color: "#999",
                      fontSize: 16,
                      cursor: "pointer",
                      outline: "none",
                      padding: 0,
                      lineHeight: 1,
                    }}
                    aria-label="Remove"
                  >
                    ×
                  </button>
                </span>
              ))}
              <input
                id="chip-input"
                ref={chipInputRef}
                style={{
                  border: "none",
                  background: "transparent",
                  fontSize: 15,
                  outline: "none",
                  minWidth: 45,
                  marginLeft: 2,
                  marginRight: 2,
                  padding: 0,
                  color: "#23406a",
                  flex: "1 1 90px",
                }}
                type="text"
                value={chipInput}
                disabled={loading}
                placeholder={
                  infoChips.length === 0 ? "e.g. price, lead time" : ""
                }
                onChange={(e) => setChipInput(e.target.value)}
                onBlur={handleChipInput}
                onKeyDown={handleChipInput}
              />
            </div>
          </div>
          {/* Button swaps between Search and Stop depending on loading */}
          {!loading ? (
            <button
              type="submit"
              disabled={!request.trim()}
              style={{
                background: "linear-gradient(90deg, #2a62f7, #4b89ff)",
                color: "#fff",
                border: "none",
                padding: "0.7rem 2rem",
                fontWeight: 600,
                fontSize: 17,
                borderRadius: "0.7rem",
                cursor: !request.trim() ? "not-allowed" : "pointer",
                boxShadow: "0 2px 10px 0 rgba(70,120,220,0.07)",
              }}
            >
              Search
            </button>
          ) : (
            <button
              type="button"
              onClick={handleStop}
              style={{
                background: "linear-gradient(90deg, #e63e3e, #e68453)",
                color: "#fff",
                border: "none",
                padding: "0.7rem 2rem",
                fontWeight: 600,
                fontSize: 17,
                borderRadius: "0.7rem",
                cursor: "pointer",
                boxShadow: "0 2px 10px 0 rgba(70,120,220,0.07)",
              }}
            >
              {status.startsWith("Stopped") ? "Stopped" : "Stop"}
            </button>
          )}
        </form>

        {/* Progress bar and status */}
        {loading && (
          <div style={{ marginBottom: 14 }}>
            <div
              style={{
                height: 14,
                background: "#e3ecfb",
                borderRadius: 7,
                overflow: "hidden",
                marginBottom: 6,
              }}
            >
              <div
                style={{
                  height: "100%",
                  width: `${percent}%`,
                  background: "linear-gradient(90deg, #3e73f1, #5cc8fa)",
                  transition: "width 0.35s cubic-bezier(.5,1.5,.4,1)",
                }}
              />
            </div>
            <div
              style={{
                color: "#2a62f7",
                fontWeight: 500,
                textAlign: "center",
                fontSize: 15,
              }}
            >
              {status} {percent ? `(${percent}%)` : ""}
            </div>
          </div>
        )}

        {error && (
          <div
            style={{
              background: "#fff5f5",
              color: "#d32f2f",
              padding: "10px 16px",
              borderRadius: "0.5rem",
              marginBottom: 14,
              fontSize: 15,
              textAlign: "center",
            }}
          >
            {error}
          </div>
        )}

        {/* Results */}
        {results && (
          <div>
            <h3
              style={{
                fontSize: 21,
                color: "#23406a",
                margin: "1.2rem 0 0.6rem 0",
              }}
            >
              Offers Found
            </h3>
            {!results.offers || results.offers.length === 0 ? (
              <div
                style={{
                  fontSize: 16,
                  color: "#888",
                  marginBottom: 16,
                }}
              >
                No quotes found.
              </div>
            ) : (
              results.offers.map((o, i) => (
                <div
                  key={i}
                  style={{
                    background: "#f5f8fe",
                    border: "1px solid #e4ecfa",
                    borderRadius: "1rem",
                    padding: "1rem 1.1rem 0.9rem 1.1rem",
                    marginBottom: 14,
                    boxShadow: "0 2px 8px rgba(110,130,220,.05)",
                  }}
                >
                  <div style={{ fontWeight: 600, color: "#183c7a" }}>
                    {o.supplier || "Unknown Supplier"}
                  </div>
                  <div style={{ fontSize: 15, color: "#4b5997" }}>
                    Part: <b>{o.part_number || "N/A"}</b>
                  </div>
                  {o.equivalent && (
                    <div style={{ fontSize: 15, color: "#4b5997" }}>
                      Equivalent: <b>{o.equivalent}</b>
                    </div>
                  )}
                  <div style={{ fontSize: 15 }}>
                    Quantity: {o.quantity || "N/A"}
                  </div>
                  <div style={{ fontSize: 15 }}>
                    Price:{" "}
                    <b>{o.price ? `${o.price} ${o.currency || ""}` : "N/A"}</b>
                  </div>
                  {o.images && o.images.length > 0 && (
                    <div style={{ margin: "10px 0" }}>
                      <div
                        style={{
                          fontSize: 14,
                          fontWeight: 500,
                          color: "#3754ad",
                        }}
                      >
                        Images / Diagrams:
                      </div>
                      {o.images_description && (
                        <div
                          style={{
                            fontSize: 13,
                            color: "#455088",
                            marginBottom: 5,
                          }}
                        >
                          {o.images_description}
                        </div>
                      )}
                      <div
                        style={{
                          display: "flex",
                          gap: 12,
                          flexWrap: "wrap",
                          marginTop: 5,
                        }}
                      >
                        {o.images.map((img, idx) => (
                          <a
                            href={img}
                            key={idx}
                            target="_blank"
                            rel="noopener noreferrer"
                          >
                            <img
                              src={img.startsWith("http") ? img : undefined}
                              alt="diagram"
                              style={{
                                maxWidth: 120,
                                maxHeight: 120,
                                borderRadius: 7,
                                border: "1px solid #c5d4ed",
                                background: "#fff",
                                boxShadow: "0 1px 4px rgba(30,30,70,.07)",
                              }}
                            />
                          </a>
                        ))}
                      </div>
                    </div>
                  )}
                  {o.context && (
                    <details
                      style={{
                        fontSize: 13,
                        color: "#3d4370",
                        marginTop: 7,
                        marginBottom: 7,
                      }}
                    >
                      <summary style={{ cursor: "pointer" }}>
                        Show context
                      </summary>
                      <div style={{ whiteSpace: "pre-wrap", marginTop: 4 }}>
                        {o.context.slice(0, 800) +
                          (o.context.length > 800 ? "..." : "")}
                      </div>
                    </details>
                  )}
                  <a
                    href={o.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    style={{
                      color: "#2961e8",
                      fontWeight: 500,
                      fontSize: 15,
                      textDecoration: "none",
                    }}
                  >
                    Source
                  </a>
                </div>
              ))
            )}

            <h3
              style={{
                fontSize: 20,
                color: "#23406a",
                margin: "1.1rem 0 0.7rem 0",
              }}
            >
              Useful Websites
            </h3>
            <ul style={{ paddingLeft: 22, margin: "0 0 0.5rem 0" }}>
              {results.useful_sites && results.useful_sites.length > 0 ? (
                results.useful_sites.map((u, i) => (
                  <li key={i} style={{ marginBottom: 5, fontSize: 15 }}>
                    <a
                      href={u}
                      target="_blank"
                      rel="noopener noreferrer"
                      style={{ color: "#2576e8" }}
                    >
                      {u}
                    </a>
                  </li>
                ))
              ) : (
                <li style={{ color: "#888" }}>No useful sites found.</li>
              )}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}

export default App;
