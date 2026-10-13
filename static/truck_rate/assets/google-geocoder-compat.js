(() => {
  const originalAppendChild = HTMLHeadElement.prototype.appendChild;

  HTMLHeadElement.prototype.appendChild = function appendChildWithGeocoderCompatibility(node) {
    const isMapsScript = node?.tagName === "SCRIPT" &&
      String(node.src || "").includes("maps.googleapis.com/maps/api/js");

    if (isMapsScript) {
      node.addEventListener("load", () => {
        const prototype = window.google?.maps?.Geocoder?.prototype;
        if (!prototype || prototype.__promiseCompatibilityApplied) return;

        const originalGeocode = prototype.geocode;
        prototype.geocode = function geocodeWithPromise(request, callback) {
          if (typeof callback === "function") {
            return originalGeocode.call(this, request, callback);
          }
          return new Promise((resolve, reject) => {
            originalGeocode.call(this, request, (results, status) => {
              if (status === "OK") {
                resolve({ results });
                return;
              }
              reject(new Error(`Google Maps geocoding failed: ${status || "unknown error"}`));
            });
          });
        };
        prototype.__promiseCompatibilityApplied = true;
      }, { once: true });
    }

    return originalAppendChild.call(this, node);
  };
})();
