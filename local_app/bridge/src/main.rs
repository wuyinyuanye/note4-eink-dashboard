//! One persistent SDK session. JSON lines on stdin/stdout, one event consumer.
use std::{io::{self, Write}, sync::Arc};
use tokio::io::{AsyncBufReadExt, BufReader};
use serde_json::{json, Value};
use zectrix_sdk::{Device, Event, ImageFormat, OpenOptions, Error};
fn emit(value: Value) { let stdout=io::stdout(); let mut out=stdout.lock(); let _=writeln!(out,"{value}"); let _=out.flush(); }
fn image_format(value: &str) -> Result<ImageFormat, &'static str> {
    match value { "Mono" => Ok(ImageFormat::Mono), "Gray4" => Ok(ImageFormat::Gray4), "Gray16" => Ok(ImageFormat::Gray16), _ => Err("unsupported image format") }
}
#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args:Vec<_>=std::env::args().collect();
    if !(4..=5).contains(&args.len()) { return Err("bridge <target> <expected-id> <initial-png> [Mono|Gray4|Gray16]".into()); }
    image_format(args.get(4).map(String::as_str).unwrap_or("Gray16"))?;
    // Decode before opening; Python also probes INFO and checks identity first.
    let initial=std::fs::read(&args[3])?;
    if initial.len()>8*1024*1024 { return Err("initial PNG too large".into()); }
    let decoded=image::load_from_memory_with_format(&initial,image::ImageFormat::Png)?;
    if decoded.width()!=400 || decoded.height()!=300 { return Err("initial PNG must be 400x300".into()); }
    let device=Arc::new(Device::open(OpenOptions{target:Some(args[1].clone()),client_name:Some("Note4 Weather".into())}).await?);
    if !device.summary.id.eq_ignore_ascii_case(&args[2]) { device.close().await?; return Err("device identity mismatch".into()); }
    emit(json!({"event":"connected","device":device.summary,"gray16":device.palette().gray16,"gray4":device.palette().gray4}));
    let events=device.clone();
    let consumer=tokio::spawn(async move {
        loop {
            match events.next_event().await {
                Ok(Some(Event::Key{key,action}))=>emit(json!({"event":"key","key":key,"action":action})),
                Ok(Some(Event::RecordingStarted))=>emit(json!({"event":"recording_started"})),
                Ok(Some(Event::RecordingFinished{audio,reason}))=>emit(json!({"event":"recording_finished","samples":audio.map(|a|a.samples.len()),"reason":reason})),
                Ok(Some(Event::Disconnected{reason}))=>{emit(json!({"event":"disconnected","reason":reason}));break},
                Ok(None)=>break,
                Err(e)=>{emit(json!({"event":"disconnected","error":e.to_string()}));break}
            }
        }
    });
    async fn show(device:&Device, png:&[u8], id:Value, format:ImageFormat) -> bool {
        let result=device.show_with_format(png,format).await;
        match result {
            Ok(())=>{emit(json!({"event":"displayed","id":id,"wire_format":format!("{format:?}")}));true},
            Err(e)=>{let unknown=matches!(e,Error::ResultUnknown);emit(json!({"event":"display_error","id":id,"error":e.to_string(),"kind":format!("{e:?}"),"unknown":unknown}));false}
        }
    }
    // The application asks for status first, builds the image with the battery
    // value, and then requests exactly one initial display.
    let mut lines=BufReader::new(tokio::io::stdin()).lines();
    loop {
        let line=tokio::select! {line=lines.next_line()=>line?, _=tokio::signal::ctrl_c()=>None};
        let Some(line)=line else {break};
        let command:Value=match serde_json::from_str(&line) {Ok(c)=>c,Err(_)=>continue};
        match command["command"].as_str() {
            Some("show")=> {
                let format=match image_format(command["format"].as_str().unwrap_or("Gray16")) {
                    Ok(value)=>value,
                    Err(error)=>{emit(json!({"event":"display_error","id":command["id"],"error":error,"kind":"InvalidFormat"}));continue;}
                };
                let result=command["path"].as_str().ok_or("missing path").and_then(|p|std::fs::read(p).map_err(|_|"unreadable png"));
                match result {Ok(bytes)=>{show(&device,&bytes,command["id"].clone(),format).await;},Err(e)=>emit(json!({"event":"display_error","id":command["id"],"error":e}))}
            },
            Some("status")=>match device.status().await {Ok(s)=>emit(json!({"event":"status","status":s})),Err(e)=>emit(json!({"event":"status_error","error":e.to_string()}))},
            Some("close")=>break,
            _=>{}
        }
    }
    let _=device.close().await; consumer.abort(); Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn page_format_is_real_wire_bit_depth() {
        assert_eq!(image_format("Mono").unwrap() as u8,1);
        assert_eq!(image_format("Gray4").unwrap() as u8,2);
        assert_eq!(image_format("Gray16").unwrap() as u8,4);
        assert!(image_format("Gray3").is_err());
    }
}
