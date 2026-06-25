const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export interface RouteSearchRequest {
  from_station: string;
  to_station: string;
  date: string;
  max_transfers: number;
  min_transfer_minutes: number;
}

export interface TrainSegment {
  train_no: string;
  from_station: string;
  to_station: string;
  depart_at: string;
  arrive_at: string;
  duration_minutes: number;
  prices: Array<{ seat_type: string; price: string; remaining: string }>;
  source: string;
  updated_at: string;
}

export interface TransferPlan {
  total_price: string;
  total_duration_minutes: number;
  transfer_minutes: number;
  transfer_stations: string[];
  segments: TrainSegment[];
}

export interface RouteSearchResponse {
  query_id: string;
  source: string;
  updated_at: string;
  plans: TransferPlan[];
}

export interface StationSearchResponse {
  stations: string[];
}

export async function searchStations(query: string): Promise<string[]> {
  const params = new URLSearchParams({ q: query });
  const response = await fetch(
    `${API_BASE_URL}/api/stations/search?${params.toString()}`,
  );

  if (!response.ok) {
    throw new Error(`站点查询失败：${response.status}`);
  }

  const body: StationSearchResponse = await response.json();
  return body.stations;
}

export async function searchRoutes(
  payload: RouteSearchRequest,
): Promise<RouteSearchResponse> {
  const response = await fetch(`${API_BASE_URL}/api/routes/search`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    throw new Error(`查询失败：${response.status}`);
  }

  return response.json();
}
