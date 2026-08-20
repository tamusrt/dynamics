for n = 0:3
    w = tcpclient("india.colorado.edu", 13);
    while w.BytesAvailable <= 0
    end
    
    % char(read(w))
    % jsondecode('{"time":"<timestamp>", "model": "<identifier>", "id": 5, "temperature_C": 2.3, "humidity": 7, "wind_dir_deg": 68, "wind_speed_ms": 5.8, "gust_speed_ms": 7.98, "rainfall_mm": 23.04, "uv": 145, "uvi": 2, "light_lux": 5.98, "battery":"<status>", "mix": "<crc>"}')
    % jsondecode(char(read(w)))
    pause(3)
end