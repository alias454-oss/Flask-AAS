(function () {
  "use strict";

  async function refreshZones(countrySelect) {
    const root = countrySelect.closest("[data-location-fields]");
    if (!root) {
      return;
    }

    const zoneSelect = root.querySelector("[data-location-zone]");
    if (!zoneSelect) {
      return;
    }

    const endpoint = zoneSelect.dataset.zonesUrl;
    const countryCode = countrySelect.value;
    const previousValue = zoneSelect.value;

    zoneSelect.replaceChildren(new Option("Loading subdivisions…", ""));
    zoneSelect.disabled = true;

    if (!countryCode || !endpoint) {
      zoneSelect.replaceChildren(new Option("Select country first", ""));
      return;
    }

    try {
      const url = new URL(endpoint, window.location.origin);
      url.searchParams.set("country", countryCode);
      const response = await fetch(url, {
        credentials: "same-origin",
        headers: {"Accept": "application/json"}
      });
      if (!response.ok) {
        throw new Error("Subdivision lookup failed");
      }

      const payload = await response.json();
      const zones = Array.isArray(payload.zones) ? payload.zones : [];
      const placeholder = zones.length ? "Select subdivision" : "No ISO subdivision available";
      zoneSelect.replaceChildren(new Option(placeholder, ""));

      zones.forEach(function (zone) {
        zoneSelect.add(new Option(zone.label, zone.code));
      });

      if (zones.some(function (zone) { return zone.code === previousValue; })) {
        zoneSelect.value = previousValue;
      }
      zoneSelect.disabled = false;
    } catch (error) {
      zoneSelect.replaceChildren(new Option("Subdivision data unavailable", ""));
      zoneSelect.disabled = false;
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-location-country]").forEach(function (countrySelect) {
      countrySelect.addEventListener("change", function () {
        refreshZones(countrySelect);
      });
    });
  });
}());
