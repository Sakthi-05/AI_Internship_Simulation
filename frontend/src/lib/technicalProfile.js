export async function loadTechnicalProfile(apiClient) {
  const [catalogResponse, profileResponse] = await Promise.all([
    apiClient.get("/api/technical-domains"),
    apiClient.get("/api/students/me/technical-profile"),
  ]);
  return {
    domains: catalogResponse.data?.domains || [],
    profile: profileResponse.data,
  };
}

export function getDomainTechnologies(domains, domainId) {
  return domains.find((domain) => domain.id === domainId)?.technologies || [];
}

export function technicalProfileState(profile) {
  if (!profile?.selected) return "unselected";
  if (profile.status === "active") return "active";
  if (profile.status === "unavailable") return "unavailable";
  return "unavailable";
}

export function ratingsFromTechnicalProfile(profile) {
  return Object.fromEntries(
    (profile?.technology_ratings || [])
      .filter((item) => Number.isInteger(item.self_rating))
      .map((item) => [item.technology_id, item.self_rating]),
  );
}

export function buildTechnicalProfilePayload(domainId, technologies, ratings) {
  if (!domainId || !technologies.length) {
    throw new Error("Choose a technical domain before saving.");
  }
  const technologyRatings = technologies.map((technology) => {
    const self_rating = ratings[technology.id];
    if (!Number.isInteger(self_rating) || self_rating < 1 || self_rating > 5) {
      throw new Error("Rate every technology from 1 to 5 before saving.");
    }
    return { technology_id: technology.id, self_rating };
  });
  return { technical_domain_id: domainId, technology_ratings: technologyRatings };
}

export async function saveTechnicalProfile(apiClient, payload) {
  const response = await apiClient.put("/api/students/me/technical-profile", payload);
  return response.data;
}
